"""The conversation as the wire transports see it, and how it survives a change of model.

Sessions journal an assistant turn as ordered *parts* — ``text``, ``reasoning`` and
``tool_call`` — so a provider's native reasoning (Anthropic's signed thinking
blocks, OpenAI's encrypted reasoning items) is replayed exactly as it was received.
:func:`transform_messages` decides what of that history a given model may see:
native reasoning only goes back to the model that produced it; for any other
model it becomes plain text (or is dropped when opaque), tool call ids are
re-spelled for the target protocol, and a tool call left without a result gets a
synthetic one so every protocol accepts the history.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from supervisor.models.spec import ModelSpec, WireApi
from supervisor.seams.core.types import ChatMessage, invalid_arguments

TEXT, REASONING, TOOL_CALL = "text", "reasoning", "tool_call"
NO_RESULT = "No result provided"
IMAGE_OMITTED = "(image omitted: model does not support images)"
_CHARS_PER_TOKEN = 4
_IMAGE_CHARS = 4800


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    signature: str | None = None
    """Responses: the output message's id (and phase), so a replay keeps its identity."""


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    thinking: str
    signature: str | None = None
    """Anthropic: the thinking signature (or redacted data). Responses: the reasoning item
    as JSON."""
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    id: str
    name: str
    arguments: Mapping[str, Any]


type AssistantBlock = TextBlock | ThinkingBlock | ToolCallBlock


@dataclass(frozen=True, slots=True)
class Image:
    mime_type: str
    data: str
    """Base64, without the ``data:`` prefix."""

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    images: tuple[Image, ...] = ()


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    blocks: tuple[AssistantBlock, ...]
    model: str | None = None
    """The model id that produced this turn."""
    api: WireApi | None = None
    """The protocol its native reasoning is spelled in, when it has any."""
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    text: str
    is_error: bool = False


type Message = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Context:
    system_prompt: str = ""
    messages: tuple[Message, ...] = ()
    tools: tuple[Tool, ...] = field(default_factory=tuple)


# -- journal parts <-> blocks --------------------------------------------------


def blocks_to_parts(
    blocks: Iterable[AssistantBlock], *, api: WireApi, model: str
) -> list[dict[str, Any]]:
    """An assistant turn as journal parts; reasoning parts remember who can read them back."""
    parts: list[dict[str, Any]] = []
    for block in blocks:
        match block:
            case TextBlock(text, signature):
                parts.append(
                    {"type": TEXT, "text": text, **({"signature": signature} if signature else {})}
                )
            case ThinkingBlock(thinking, signature, redacted):
                parts.append(
                    {
                        "type": REASONING,
                        "text": thinking,
                        "signature": signature,
                        "redacted": redacted,
                        "api": api.value,
                        "model": model,
                    }
                )
            case ToolCallBlock(call_id, name, arguments):
                parts.append(
                    {"type": TOOL_CALL, "id": call_id, "name": name, "arguments": dict(arguments)}
                )
    return parts


def _assistant(message: ChatMessage) -> AssistantMessage:
    if not message.parts:
        blocks: list[AssistantBlock] = [TextBlock(message.content)] if message.content else []
        blocks += [_tool_call(call) for call in message.tool_calls or ()]
        return AssistantMessage(tuple(blocks), model=message.model)
    api: WireApi | None = None
    blocks = []
    for part in message.parts:
        kind = part.get("type")
        if kind == TEXT:
            blocks.append(TextBlock(str(part.get("text", "")), part.get("signature")))
        elif kind == REASONING:
            api = api or _api(part.get("api"))
            blocks.append(
                ThinkingBlock(
                    str(part.get("text", "")),
                    part.get("signature"),
                    bool(part.get("redacted", False)),
                )
            )
        elif kind == TOOL_CALL:
            blocks.append(_tool_call(part))
    return AssistantMessage(tuple(blocks), model=message.model, api=api)


def _api(value: object) -> WireApi | None:
    try:
        return WireApi(str(value))
    except ValueError:
        return None


def _tool_call(call: Mapping[str, Any]) -> ToolCallBlock:
    function = call.get("function")
    if isinstance(function, Mapping):
        name, raw = function.get("name"), function.get("arguments")
    else:
        name, raw = call.get("name"), call.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            raw = invalid_arguments(raw)
    return ToolCallBlock(
        str(call.get("id") or ""),
        str(name or ""),
        raw if isinstance(raw, dict) else invalid_arguments(json.dumps(raw)),
    )


def _tool_failed(text: str) -> bool:
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(result, dict):
        return False
    return result.get("ok") is False or ("ok" not in result and bool(result.get("error")))


def context_from_chat(
    messages: Sequence[ChatMessage], tools: Sequence[Mapping[str, Any]]
) -> Context:
    """The session's chat messages as a transport context.

    Leading system messages are the system prompt; a system message later in the
    conversation (a memory note, a background result) is shown as a user message.
    """
    system: list[str] = []
    body: list[Message] = []
    names: dict[str, str] = {}
    for message in messages:
        if message.role == "system" and not body:
            system.append(message.content)
        elif message.role in ("system", "user"):
            body.append(UserMessage(message.content))
        elif message.role == "assistant":
            assistant = _assistant(message)
            names.update({b.id: b.name for b in assistant.blocks if isinstance(b, ToolCallBlock)})
            body.append(assistant)
        elif message.role == "tool":
            call_id = message.tool_call_id or ""
            body.append(
                ToolResultMessage(
                    call_id, names.get(call_id, ""), message.content, _tool_failed(message.content)
                )
            )
    return Context(
        system_prompt="\n\n".join(s for s in system if s),
        messages=tuple(body),
        tools=tuple(_tool(t) for t in tools),
    )


def _tool(schema: Mapping[str, Any]) -> Tool:
    function = schema.get("function", schema)
    return Tool(
        name=str(function.get("name", "")),
        description=str(function.get("description") or ""),
        parameters=function.get("parameters") or {"type": "object", "properties": {}},
    )


# -- cross-model replay ----------------------------------------------------------


def same_model(message: AssistantMessage, spec: ModelSpec) -> bool:
    return message.model == spec.id and (message.api is None or message.api is spec.api)


type IdNormalizer = Callable[[str, AssistantMessage], str]


def transform_messages(
    messages: Sequence[Message], spec: ModelSpec, normalize_id: IdNormalizer | None = None
) -> list[Message]:
    """What of the history ``spec`` may be shown, in a shape its protocol accepts."""
    renamed: dict[str, str] = {}
    first: list[Message] = []
    sees_images = "image" in spec.input
    for message in messages:
        if isinstance(message, UserMessage) and message.images and not sees_images:
            first.append(UserMessage("\n".join(t for t in (message.text, IMAGE_OMITTED) if t)))
        elif isinstance(message, ToolResultMessage):
            new_id = renamed.get(message.tool_call_id)
            first.append(replace(message, tool_call_id=new_id) if new_id else message)
        elif isinstance(message, AssistantMessage):
            first.append(_for_model(message, spec, normalize_id, renamed))
        else:
            first.append(message)

    result: list[Message] = []
    pending: list[ToolCallBlock] = []
    answered: set[str] = set()

    def close_pending() -> None:
        nonlocal pending, answered
        for call in pending:
            if call.id not in answered:
                result.append(ToolResultMessage(call.id, call.name, NO_RESULT, is_error=True))
        pending, answered = [], set()

    for message in first:
        if isinstance(message, AssistantMessage):
            close_pending()
            if message.stop_reason in ("error", "aborted"):
                continue
            calls = [b for b in message.blocks if isinstance(b, ToolCallBlock)]
            if calls:
                pending, answered = calls, set()
            result.append(message)
        elif isinstance(message, ToolResultMessage):
            answered.add(message.tool_call_id)
            result.append(message)
        else:
            close_pending()
            result.append(message)
    close_pending()
    return result


def _for_model(
    message: AssistantMessage,
    spec: ModelSpec,
    normalize_id: IdNormalizer | None,
    renamed: dict[str, str],
) -> AssistantMessage:
    same = same_model(message, spec)
    blocks: list[AssistantBlock] = []
    for block in message.blocks:
        match block:
            case ThinkingBlock(thinking, signature, redacted):
                if redacted:
                    if same:
                        blocks.append(block)
                elif same and signature:
                    blocks.append(block)
                elif not thinking.strip():
                    continue
                else:
                    blocks.append(block if same else TextBlock(thinking))
            case TextBlock(text, _):
                blocks.append(block if same else TextBlock(text))
            case ToolCallBlock(call_id, _, _):
                if not same and normalize_id is not None:
                    new_id = normalize_id(call_id, message)
                    if new_id != call_id:
                        renamed[call_id] = new_id
                        block = replace(block, id=new_id)
                blocks.append(block)
    return replace(message, blocks=tuple(blocks))


# -- sizing ------------------------------------------------------------------------


def estimate_context_tokens(context: Context) -> int:
    """A characters-per-token estimate of everything sent (system prompt and tools included)."""
    chars = len(context.system_prompt)
    if context.tools:
        chars += len(json.dumps([_tool_json(t) for t in context.tools], default=str))
    for message in context.messages:
        match message:
            case UserMessage(text, images):
                chars += len(text) + _IMAGE_CHARS * len(images)
            case ToolResultMessage(_, _, text, _):
                chars += len(text)
            case AssistantMessage(blocks):
                for block in blocks:
                    match block:
                        case TextBlock(text, _):
                            chars += len(text)
                        case ThinkingBlock(thinking, _, _):
                            chars += len(thinking)
                        case ToolCallBlock(_, name, arguments):
                            chars += len(name) + len(json.dumps(arguments, default=str))
    return math.ceil(chars / _CHARS_PER_TOKEN)


def _tool_json(tool: Tool) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "parameters": dict(tool.parameters)}


CONTEXT_SAFETY_TOKENS = 4096


MIN_REPLY_TOKENS = 1024
"""A call that leaves the model less room than this to answer is not sent."""


def safety_tokens(window: int) -> int:
    """The margin kept for the estimate's error: 4,096 tokens, at most an eighth of the window."""
    return min(CONTEXT_SAFETY_TOKENS, window // 8)


def fits_with_reply(window: int) -> int:
    """The largest prompt that still leaves room for an answer (compaction keeps under it)."""
    return window - safety_tokens(window) - MIN_REPLY_TOKENS


class ContextWindowFull(Exception):
    """The prompt leaves the model no room to answer: compact, or start a new session.

    Raised instead of sending a call capped to a handful of output tokens, which
    the model ends at once (``length``) with an empty answer that looks like a
    normal one.
    """

    code = "CONTEXT_WINDOW_FULL"

    def __init__(self, *, prompt_tokens: int, window: int, model: str) -> None:
        super().__init__(
            f"the conversation (~{prompt_tokens} tokens) fills {model}'s {window}-token window: "
            "no room is left for an answer; it is compacted at the next message, or start a new session"
        )
        self.prompt_tokens = prompt_tokens
        self.window = window


def clamp_max_tokens(spec: ModelSpec, context: Context, max_tokens: int) -> int:
    """Cap the output so prompt + output (+ a safety margin) fit the context window.

    Raises ``ContextWindowFull`` when what is left is too little for an answer.
    """
    if spec.context_window <= 0:
        return max(1, max_tokens)
    prompt = estimate_context_tokens(context)
    available = spec.context_window - prompt - safety_tokens(spec.context_window)
    if available < min(max_tokens, MIN_REPLY_TOKENS):
        raise ContextWindowFull(prompt_tokens=prompt, window=spec.context_window, model=spec.id)
    return min(max_tokens, available)
