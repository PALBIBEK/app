"""Ask the user a question with options (Claude / DeepSeek ask_user_question).

The model must not invent a quiz in free text. It calls this tool; the UI
blocks until the human answers; the answer returns as a normal tool result
and the loop continues. DeepSeek: packages/interaction/tool-ask-user;
types: packages/interaction/user-questions/src/types.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import AskUserError
from .base import Tool, ToolContext, ToolResult, fail, ok


@dataclass
class AskOption:
    label: str
    description: str = ""


@dataclass
class AskQuestion:
    id: str
    question: str
    header: str = ""
    options: list[AskOption] = field(default_factory=list)
    multi_select: bool = False


@dataclass
class AskAnswerItem:
    id: str
    selected: list[str]
    custom: str | None = None


@dataclass
class AskAnswer:
    answers: list[AskAnswerItem]


class AskUser(Protocol):
    def __call__(self, questions: list[AskQuestion]) -> AskAnswer: ...


# Match DeepSeek tool-ask-user wording, plus when-to-reach-for (like remember).
# The full string goes to the model via tools.to_wire(); the system prompt only
# keeps a one-line snippet of the first sentence.
DESCRIPTION = (
    "Ask the user a concise question when you need confirmation, a choice, "
    "or missing information before proceeding. Prefer this over guessing. "
    "Send one or more questions, each with a stable id that will be echoed "
    "in the answer."
)

# DeepSeek-style structured output contract. Enforced on success payloads and
# advertised on the Tool so callers can discover it without reading details.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "selected": {"type": "array", "items": {"type": "string"}},
                    "custom": {"type": "string"},
                },
                "required": ["id", "selected"],
            },
        },
    },
    "required": ["answers"],
}


def fail_coded(message: str, code: str, **details: Any) -> ToolResult:
    """Fail so the model sees the code (loop only forwards result.text)."""
    return fail(f"Error [{code}]: {message}", code=code, **details)


def answer_payload(answer: AskAnswer) -> dict[str, Any]:
    return {
        "answers": [
            {
                "id": item.id,
                "selected": list(item.selected),
                **({"custom": item.custom} if item.custom else {}),
            }
            for item in answer.answers
        ]
    }


def enforce_output_payload(payload: Any) -> dict[str, Any]:
    """Reject success payloads that do not match OUTPUT_SCHEMA."""
    if not isinstance(payload, dict):
        raise AskUserError("output must be an object", "BAD_ANSWER")
    if set(payload.keys()) - {"answers"}:
        raise AskUserError("output has unexpected fields", "BAD_ANSWER")
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise AskUserError("output.answers must be an array", "BAD_ANSWER")
    for i, item in enumerate(answers):
        if not isinstance(item, dict):
            raise AskUserError(f"answers[{i}] must be an object", "BAD_ANSWER")
        extra = set(item.keys()) - {"id", "selected", "custom"}
        if extra:
            raise AskUserError(
                f"answers[{i}] has unexpected fields: {sorted(extra)}",
                "BAD_ANSWER")
        if "id" not in item or "selected" not in item:
            raise AskUserError(
                f"answers[{i}] requires id and selected", "BAD_ANSWER")
        if not isinstance(item["id"], str):
            raise AskUserError(f"answers[{i}].id must be a string", "BAD_ANSWER")
        if not isinstance(item["selected"], list) or not all(
                isinstance(x, str) for x in item["selected"]):
            raise AskUserError(
                f"answers[{i}].selected must be a string array", "BAD_ANSWER")
        if "custom" in item and not isinstance(item["custom"], str):
            raise AskUserError(
                f"answers[{i}].custom must be a string", "BAD_ANSWER")
    return payload


def validate_answer(questions: list[AskQuestion], answer: AskAnswer) -> None:
    if not isinstance(answer, AskAnswer):
        raise AskUserError("ask_user returned an invalid answer shape", "BAD_ANSWER")
    by_id = {q.id: q for q in questions}
    seen: set[str] = set()
    for item in answer.answers:
        if item.id not in by_id:
            raise AskUserError(f"unknown answer id {item.id!r}", "BAD_ANSWER")
        if item.id in seen:
            raise AskUserError(f"duplicate answer id {item.id!r}", "BAD_ANSWER")
        seen.add(item.id)
        q = by_id[item.id]
        labels = {o.label for o in q.options}
        if not q.multi_select and len(item.selected) > 1:
            raise AskUserError(
                f"question {item.id!r} is single-select but got multiple labels",
                "BAD_ANSWER")
        for label in item.selected:
            if labels and label not in labels:
                raise AskUserError(
                    f"selected label {label!r} is not an option for {item.id}",
                    "BAD_ANSWER")
        if not item.selected and not (item.custom or "").strip():
            raise AskUserError(f"empty answer for {item.id}", "BAD_ANSWER")
    missing = [q.id for q in questions if q.id not in seen]
    if missing:
        raise AskUserError(
            f"missing answers for: {', '.join(missing)}", "BAD_ANSWER")


def _parse_questions(raw: Any) -> list[AskQuestion]:
    if not isinstance(raw, list) or not raw:
        raise AskUserError(
            "ask_user_question requires a non-empty `questions` array",
            "EMPTY_QUESTIONS")
    parsed: list[AskQuestion] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AskUserError(
                f"questions[{i}] must be an object", "BAD_ARGS")
        qid = str(item.get("id") or "").strip()
        text = str(item.get("question") or "").strip()
        if not qid or not text:
            raise AskUserError(
                f"questions[{i}] needs `id` and `question`", "BAD_ARGS")
        options: list[AskOption] = []
        for opt in item.get("options") or []:
            if not isinstance(opt, dict) or not str(opt.get("label") or "").strip():
                raise AskUserError(
                    f"questions[{i}].options must be objects with `label`",
                    "BAD_ARGS")
            options.append(AskOption(
                label=str(opt["label"]).strip(),
                description=str(opt.get("description") or "").strip(),
            ))
        parsed.append(AskQuestion(
            id=qid,
            question=text,
            header=str(item.get("header") or "").strip(),
            options=options,
            multi_select=bool(item.get("multi_select")),
        ))
    return parsed


def ask_user_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            parsed = _parse_questions(args.get("questions"))
        except AskUserError as exc:
            return fail_coded(str(exc), exc.code)

        # Owned children must not quiz the human (DeepSeek DELEGATED_CALLER).
        if ctx.extra.get("delegated"):
            return fail_coded(
                "human interaction is unavailable while this agent is owned "
                "by another live agent; include the unresolved question or "
                "decision in the child agent's final result",
                "DELEGATED_CALLER",
            )

        interrupt = ctx.extra.get("interrupt")
        if interrupt is not None and getattr(interrupt, "is_set", lambda: False)():
            return fail_coded(
                "ask_user_question was aborted before the user answered",
                "ASK_ABORTED",
            )

        asker = ctx.extra.get("ask_user")
        if asker is None:
            return fail_coded(
                "no UI is available to ask the user. Do not retry; "
                "state your assumptions and continue, or wait for the user.",
                "NO_PROVIDER",
            )

        try:
            answer = asker(parsed)
            validate_answer(parsed, answer)
            payload = enforce_output_payload(answer_payload(answer))
            return ok(
                json.dumps(payload),
                answers=payload["answers"],
                schema=OUTPUT_SCHEMA,
            )
        except AskUserError as exc:
            # ASK_CANCELLED: user backed out — model should not treat as a hard
            # task failure. ASK_ABORTED: run interrupt — stop / wait.
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001 - tool must not kill the run
            return fail_coded(f"the user could not be asked: {exc}", "UI_ERROR")

    return Tool(
        name="ask_user_question",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Questions to ask before continuing.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable id echoed in the answer.",
                            },
                            "question": {
                                "type": "string",
                                "description": "The specific question to ask.",
                            },
                            "header": {
                                "type": "string",
                                "description": "Optional short heading.",
                            },
                            "options": {
                                "type": "array",
                                "description": (
                                    "Optional choices. If you recommend one, "
                                    "put it first and append \"(Recommended)\" "
                                    "to that label."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label"],
                                },
                            },
                            "multi_select": {
                                "type": "boolean",
                                "description": (
                                    "Whether the user may select more than one "
                                    "option. Defaults to false."
                                ),
                            },
                        },
                        "required": ["id", "question"],
                    },
                },
            },
            "required": ["questions"],
        },
        run=run,
        output_schema=OUTPUT_SCHEMA,
    )
