"""The transcript automatic capture reads (``supervisor.memory.transcript``, G1 of
``docs/memory-gaps.md``): Grok Build's ``capture_transcript.rs`` tests, ported case for
case, then ours: how a run's journal becomes items, and how long condensing takes."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, TurnEnd
from supervisor.memory import turn
from supervisor.memory.transcript import (
    BASE_MAX_TRANSCRIPT_BYTES,
    BASE_MAX_TRANSCRIPT_ITEMS,
    MIN_MAX_TRANSCRIPT_BYTES,
    MIN_MAX_TRANSCRIPT_ITEMS,
    STOPPED_MARKER,
    WITHHELD_ARGUMENTS,
    WITHHELD_RESULT,
    TranscriptBudget,
    condense,
    run_items,
    trim_middle,
)
from supervisor.seams.core.types import ModelGatewayError

FIRST = TranscriptBudget.for_attempt(1)


def env(seq: int, payload: Any) -> Envelope:
    """One journal entry of a run."""
    return Envelope(session_id="s1", seq=seq, event_id=f"e{seq}", ts_ms=seq, turn_id="r1", trace_id=None, payload=payload)


def user(text: str) -> dict[str, Any]:
    return {"type": "user", "content": [{"type": "text", "text": text}]}


def assistant(text: str, calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"type": "assistant", "content": text, "tool_calls": calls or []}


def tool_result(call_id: str, content: str) -> dict[str, Any]:
    return {"type": "tool_result", "call_id": call_id, "name": "read_file", "content": content, "is_error": False,
            "images": []}


def reasoning(text: str) -> dict[str, Any]:
    return {"type": "reasoning", "text": text}


def tool_loop(steps: int, result_bytes: int) -> list[dict[str, Any]]:
    """Grok's fixture: a request, ``steps`` rounds of reasoning, a call and its result, and
    the answer."""
    items = [user("please refactor the workflow modal")]
    for step in range(steps):
        items.append(reasoning(f"thinking about step {step}"))
        items.append(assistant(f"step {step}", [{"id": f"call-{step}", "name": "read_file",
                                                 "arguments": json.dumps({"path": f"src/file{step}.rs"})}]))
        items.append(tool_result(f"call-{step}", "x" * result_bytes))
    items.append(assistant("Done: the modal now confirms before deleting."))
    return items


def size(encoded: str) -> int:
    return len(encoded.encode("utf-8"))


def contains_all_users(encoded: str, items: list[dict[str, Any]]) -> bool:
    return all(
        json.dumps(part["text"], ensure_ascii=False)[1:-1] in encoded
        for item in items
        if item["type"] == "user"
        for part in item["content"]
        if part["type"] == "text"
    )


# --- Grok's cases -------------------------------------------------------------------------


def test_under_budget_turn_is_byte_identical() -> None:
    items = tool_loop(3, 100)

    condensed = condense(items, FIRST)

    assert condensed.json == json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    assert not condensed.stats.is_condensed()
    assert condensed.stats.prompt_note() is None


def test_encrypted_reasoning_is_stripped_even_under_budget() -> None:
    """The opaque blob only its own model can read never enters the transcript, and that is
    not condensation: the extractor is not told reasoning was omitted."""
    message = MessageEnd(role="assistant", content=[
        {"type": "reasoning", "text": "user prefers tabs", "signature": "SEALEDCIPHERTEXT", "redacted": False,
         "api": "openai-responses", "model": "m"},
        {"type": "text", "text": "Noted."},
    ])

    items = [user("tabs please"), *run_items([env(1, message)])]
    condensed = condense(items, FIRST)

    parsed = json.loads(condensed.json)
    assert parsed[1] == {"type": "reasoning", "text": "user prefers tabs"}, "the readable summary survives"
    assert "SEALEDCIPHERTEXT" not in condensed.json
    assert not condensed.stats.is_condensed()
    assert condensed.stats.prompt_note() is None


def test_tool_results_trim_head_and_tail_with_marker() -> None:
    items = tool_loop(60, 20 * 1024)

    condensed = condense(items, FIRST)

    assert size(condensed.json) <= FIRST.max_bytes
    assert condensed.stats.tool_results_trimmed > 0
    assert "bytes omitted" in condensed.json
    assert contains_all_users(condensed.json, items)
    assert "the modal now confirms before deleting" in condensed.json
    assert condensed.stats.steps_omitted == 0


def test_s4_shaped_turn_fits_first_attempt_budget() -> None:
    """Grok's real long turn: a 36 KiB request, 126 steps with reasoning every third and a
    large result every twentieth."""
    items = [user("u" * 36 * 1024)]
    for step in range(126):
        if step % 3 == 0:
            items.append(reasoning("r" * 4700))
        items.append(assistant(f"step {step}"))
        items.append(tool_result(f"c{step}", "t" * (14 * 1024 if step % 20 == 0 else 1800)))
    items.append(assistant("final summary"))
    assert size(json.dumps(items)) > BASE_MAX_TRANSCRIPT_BYTES, "the fixture must overflow"

    condensed = condense(items, FIRST)

    assert size(condensed.json) <= BASE_MAX_TRANSCRIPT_BYTES
    assert "final summary" in condensed.json
    assert condensed.stats.reasoning_dropped > 0
    assert condensed.stats.steps_omitted == 0
    assert not condensed.stats.over_budget


def test_thousand_step_turn_keeps_user_and_edges() -> None:
    items = tool_loop(1000, 3000)

    condensed = condense(items, FIRST)

    assert size(condensed.json) <= FIRST.max_bytes
    assert condensed.stats.final_items <= FIRST.max_items
    assert condensed.stats.steps_omitted > 0
    assert "intermediate steps omitted" in condensed.json
    assert "please refactor the workflow modal" in condensed.json
    assert "the modal now confirms before deleting" in condensed.json
    assert '"step 0"' in condensed.json
    assert not condensed.stats.over_budget


def test_oversized_user_text_is_trimmed_last_and_never_fails() -> None:
    items = [user("p" * 900 * 1024), assistant("short answer")]

    condensed = condense(items, FIRST)

    assert condensed.stats.user_text_trimmed >= 1
    assert size(condensed.json) <= FIRST.max_bytes
    assert "short answer" in condensed.json
    assert not condensed.stats.over_budget


def test_trim_middle_respects_char_boundaries() -> None:
    trimmed = trim_middle("é" * 1000, 100)

    assert trimmed is not None and "bytes omitted" in trimmed
    assert trimmed.startswith("é") and trimmed.endswith("é")
    assert trim_middle("short", 100) is None


# --- ours ---------------------------------------------------------------------------------


def test_each_attempt_halves_the_budget_down_to_its_floor() -> None:
    assert TranscriptBudget.for_attempt(1) == TranscriptBudget(BASE_MAX_TRANSCRIPT_BYTES, BASE_MAX_TRANSCRIPT_ITEMS)
    assert TranscriptBudget.for_attempt(2) == TranscriptBudget(BASE_MAX_TRANSCRIPT_BYTES // 2, BASE_MAX_TRANSCRIPT_ITEMS // 2)
    assert TranscriptBudget.for_attempt(30) == TranscriptBudget(MIN_MAX_TRANSCRIPT_BYTES, MIN_MAX_TRANSCRIPT_ITEMS)
    assert TranscriptBudget.for_attempt(0) == TranscriptBudget.for_attempt(1)


def test_a_runs_journal_becomes_its_steps_tool_results_included() -> None:
    """What capture used to drop: the tool calls and their results, between the request
    and the answer."""
    events = [
        MessageEnd(role="user", content=[{"type": "text", "text": "what builds this repo?"}]),
        MessageEnd(role="assistant", content=[
            {"type": "tool_call", "id": "c1", "name": "bash", "arguments": {"command": "cat Makefile"}},
        ]),
        ToolEnd(tool_call_id="c1", tool_name="bash", result={"ok": True, "payload": "check:\n\tmake lint test"}),
        MessageEnd(role="assistant", content=[{"type": "text", "text": "It builds with `make check`."}]),
    ]

    items = run_items([env(i, e) for i, e in enumerate(events)])

    assert items == [
        user("what builds this repo?"),
        assistant("", [{"id": "c1", "name": "bash", "arguments": '{"command": "cat Makefile"}'}]),
        {"type": "tool_result", "call_id": "c1", "name": "bash", "content": "check:\n\tmake lint test",
         "is_error": False, "images": []},
        assistant("It builds with `make check`."),
    ]


def test_a_failed_call_is_its_error_and_a_structured_result_is_json() -> None:
    failed = ToolEnd(tool_call_id="c1", tool_name="read", result={"ok": False, "error": "no such file"}, is_error=True)
    structured = ToolEnd(tool_call_id="c2", tool_name="ls", result={"ok": True, "payload": {"files": ["a.py"]}})

    first, second = run_items([env(1, failed), env(2, structured)])

    assert (first["content"], first["is_error"]) == ("no such file", True)
    assert json.loads(second["content"]) == {"files": ["a.py"]}


def test_a_stopped_run_ends_with_the_stop_marker() -> None:
    """A run the user stopped is captured too: its work up to the stop, marked unfinished."""
    events = [
        MessageEnd(role="user", content=[{"type": "text", "text": "find the deploy script"}]),
        ToolEnd(tool_call_id="c1", tool_name="bash", result={"ok": True, "payload": "scripts/deploy.sh"}),
        TurnEnd(stop_reason="aborted"),
    ]

    items = run_items([env(i, e) for i, e in enumerate(events)])

    assert items[-1] == assistant(STOPPED_MARKER)
    assert run_items([env(1, TurnEnd(stop_reason="stop"))]) == [], "a finished run has no marker"


def test_redacted_reasoning_and_signatures_never_reach_the_transcript() -> None:
    message = MessageEnd(role="assistant", content=[
        {"type": "reasoning", "text": "", "signature": "REDACTEDDATA", "redacted": True},
        {"type": "text", "text": "Done.", "signature": "TEXTSIG"},
    ])

    items = run_items([env(1, message)])

    assert items == [assistant("Done.")]
    assert "SIG" not in json.dumps(items) and "REDACTED" not in json.dumps(items)


def test_images_are_placeholders_whatever_the_budget() -> None:
    """A text extractor cannot read an image, and its base64 would be most of the bytes."""
    shot = ToolEnd(tool_call_id="c1", tool_name="browser_exec",
                   result={"ok": True, "payload": {"output": "ok", "images": [{"mime_type": "image/png", "data": "A" * 300_000}]}})
    pasted = MessageEnd(role="user", content=[{"type": "text", "text": "look"}, {"type": "image", "data": "B" * 1000}])

    condensed = condense(run_items([env(1, pasted), env(2, shot)]), FIRST)

    assert "AAAA" not in condensed.json and "BBBB" not in condensed.json
    assert condensed.json.count("[... image omitted ...]") == 2
    assert condensed.stats.images_dropped == 2
    assert "images omitted" in (condensed.stats.prompt_note() or "")


def test_condensing_leaves_the_callers_items_alone() -> None:
    items = tool_loop(60, 20 * 1024)
    before = json.dumps(items)

    condense(items, FIRST)

    assert json.dumps(items) == before


def test_condensing_time_is_bounded_however_long_the_turn() -> None:
    """A capture runs beside every other session's: a 1000-step turn with large results
    condenses in well under a second, and a 10,000-step one in a few, linearly."""
    began = time.perf_counter()
    condense(tool_loop(1000, 3000), FIRST)
    thousand = time.perf_counter() - began
    began = time.perf_counter()
    condensed = condense(tool_loop(10_000, 3000), FIRST)
    ten_thousand = time.perf_counter() - began

    assert size(condensed.json) <= FIRST.max_bytes
    assert thousand < 1.0, f"1000 steps took {thousand:.2f}s"
    assert ten_thousand < 15 * thousand + 0.5, f"not linear: {thousand:.2f}s then {ten_thousand:.2f}s"


# --- extraction attempts (supervisor.memory.turn._extract) --------------------------------


def _attempts(
    errors: list[Exception], monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]
) -> tuple[Any, list[str]]:
    """``turn._extract`` against a model that fails with ``errors``, in turn, then answers."""
    monkeypatch.setattr(turn, "RETRY_PAUSE_S", 0.0)
    sent: list[str] = []

    async def one_shot(system: str, content: str) -> str:
        sent.append(content)
        if len(sent) <= len(errors):
            raise errors[len(sent) - 1]
        return "SENSITIVITY: ordinary\n- Builds with make check"

    return asyncio.run(turn._extract(one_shot, "s1", items)), sent


OVERLOADED = ModelGatewayError(503, '{"detail": {"error": {"code": "UPSTREAM", "message": "overloaded"}}}')
TOO_LONG = ModelGatewayError(400, "This model's maximum context length is 128000 tokens")
REFUSED = ModelGatewayError(401, '{"detail": {"error": {"code": "AUTH_INVALID", "message": "bad key"}}}')


def test_a_failure_that_may_pass_is_retried_on_the_same_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 503 has nothing to do with length: the same transcript is sent again, nothing of a
    long turn condensed away for it."""
    extracted, sent = _attempts([OVERLOADED, OVERLOADED], monkeypatch, tool_loop(60, 20 * 1024))

    assert extracted is not None and extracted.facts == ["Builds with make check"]
    assert len(sent) == 3 and sent[0] == sent[1] == sent[2], "the same transcript each time"


def test_a_transcript_too_long_for_the_model_is_retried_on_the_shorter_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extracted, sent = _attempts([TOO_LONG], monkeypatch, tool_loop(60, 20 * 1024))

    assert extracted is not None
    assert len(sent[0].encode()) > len(sent[1].encode()), "the retry read the half budget"


def test_an_overflow_that_no_shorter_budget_would_help_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    extracted, sent = _attempts([TOO_LONG], monkeypatch, tool_loop(2, 100))

    assert extracted is None and len(sent) == 1, "the same transcript would overflow the same way"


def test_a_refusal_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad key, a spent budget, an unknown model fail the same way again, and each call
    may be charged."""
    extracted, sent = _attempts([REFUSED], monkeypatch, tool_loop(2, 100))

    assert extracted is None and len(sent) == 1


def test_a_capture_that_keeps_failing_stops_after_its_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    extracted, sent = _attempts([OVERLOADED] * 99, monkeypatch, tool_loop(2, 100))

    assert extracted is None
    assert len(sent) == turn.CAPTURE_ATTEMPTS, "a bounded number of calls"


# --- stop markers survive condensing --------------------------------------------------------


def test_a_stopped_runs_marker_survives_omitting_the_middle_steps() -> None:
    """A burst of runs, the first stopped: omitting middle steps keeps every run's stop
    marker where it was, so the extractor never reads a stopped run's work as done."""
    stopped = [*tool_loop(500, 3000)[:-1], assistant(STOPPED_MARKER)]
    finished = tool_loop(500, 3000)
    items = stopped + finished

    condensed = condense(items, TranscriptBudget.for_attempt(4))
    parsed = json.loads(condensed.json)

    assert condensed.stats.steps_omitted > 0
    contents = [item.get("content") for item in parsed if item["type"] == "assistant"]
    assert contents.count(STOPPED_MARKER) == 1
    users = [i for i, item in enumerate(parsed) if item["type"] == "user"]
    marker = next(i for i, item in enumerate(parsed) if item.get("content") == STOPPED_MARKER)
    assert users[0] < marker < users[1], "the marker ends the run it stopped, before the next run's request"
    assert "intermediate steps omitted" in parsed[marker - 1]["content"], "the first run's gap ends at its stop"


def test_a_withheld_tools_result_keeps_its_place_but_not_its_content() -> None:
    """Capture withholds the memory and history tools' results: their content is facts held
    elsewhere, with an audience of their own, that extraction would copy without it."""
    from supervisor.memory.tools import MEMORY_TOOL, PLATFORM_TOOLS

    events = [
        MessageEnd(role="user", content=[{"type": "text", "text": "what do you know about the launch?"}]),
        MessageEnd(role="assistant", content=[
            {"type": "tool_call", "id": "c1", "name": MEMORY_TOOL, "arguments": {"query": "Project Kestrel launch"}},
            {"type": "tool_call", "id": "c2", "name": "bash", "arguments": {"command": "ls"}},
        ]),
        ToolEnd(tool_call_id="c1", tool_name=MEMORY_TOOL, result={"ok": True, "payload": "Launch slips to May (private)"}),
        ToolEnd(tool_call_id="c2", tool_name="bash", result={"ok": True, "payload": "launch.md"}),
    ]

    items = run_items([env(i, e) for i, e in enumerate(events)], withheld=PLATFORM_TOOLS)

    memory, other = (i for i in items if i["type"] == "tool_result")
    assert (memory["name"], memory["content"]) == (MEMORY_TOOL, WITHHELD_RESULT)
    assert "slips" not in json.dumps(items) and "Kestrel" not in json.dumps(items), "neither its output nor its query"
    (calls,) = (i["tool_calls"] for i in items if i["type"] == "assistant")
    assert [c["arguments"] for c in calls] == [WITHHELD_ARGUMENTS, '{"command": "ls"}']
    assert other["content"] == "launch.md", "any other tool's call and result are read whole"


def test_a_burst_of_many_stopped_runs_condenses_and_an_overflow_at_its_floor_is_not_resent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst may hold 100 runs (``capture_max_turns``): every request and stop marker is
    kept, the steps between them go, and once condensing can shrink it no further an
    overflow is not sent again unchanged."""
    burst: list[dict[str, Any]] = []
    for run in range(100):
        burst += [user(f"request {run}"), *tool_loop(30, 2000)[1:-1], assistant(STOPPED_MARKER)]

    condensed = condense(burst, TranscriptBudget.for_attempt(4))
    parsed = json.loads(condensed.json)
    assert sum(i.get("content") == STOPPED_MARKER for i in parsed) == 100, "every run's stop survives"
    assert all(f"request {run}" in condensed.json for run in range(100))
    assert condensed.stats.steps_omitted > 0

    always_too_long = [TOO_LONG] * 9
    extracted, sent = _attempts(always_too_long, monkeypatch, burst)
    assert extracted is None
    assert len(sent) == len(set(sent)), "never the same transcript twice"


def test_a_huge_assistant_reply_is_trimmed_so_the_transcript_fits() -> None:
    """Ours: a reply long enough (a pasted file) outlasted every stage of Grok's and left
    the transcript over budget at every attempt; it is trimmed, head and tail, before the
    user's words are."""
    items = [user("dump the schema"), assistant("s" * 400 * 1024)]
    budget = TranscriptBudget.for_attempt(4)

    condensed = condense(items, budget)

    assert size(condensed.json) <= budget.max_bytes and not condensed.stats.over_budget
    assert condensed.stats.assistant_text_trimmed == 1 and condensed.stats.user_text_trimmed == 0
    assert "dump the schema" in condensed.json
    assert "assistant replies truncated" in (condensed.stats.prompt_note() or "")


def test_trimming_never_makes_text_longer() -> None:
    """Just over the cap, the marker would cost more than the cut saves."""
    assert trim_middle("x" * 260, 256) is None
    trimmed = trim_middle("x" * 2000, 256)
    assert trimmed is not None and len(trimmed) < 2000


def test_a_providers_retry_after_is_honoured_but_never_shortens_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(turn.asyncio, "sleep", sleep)
    monkeypatch.setattr(turn.random, "random", lambda: 0.5)  # no jitter: the pause is RETRY_PAUSE_S doubling
    now = ModelGatewayError(429, "rate limit", retry_after=0.0)
    later = ModelGatewayError(429, "rate limit", retry_after=12.0)
    forever = ModelGatewayError(429, "rate limit", retry_after=3600.0)

    async def run() -> None:
        errors = [now, later, forever]

        async def one_shot(system: str, content: str) -> str:
            if errors:
                raise errors.pop(0)
            return "SENSITIVITY: ordinary\n- ok"

        assert await turn._extract(one_shot, "s1", tool_loop(1, 10)) is not None

    asyncio.run(run())
    assert waits == [turn.RETRY_PAUSE_S, 12.0, turn.MAX_RETRY_PAUSE_S], "ours, theirs when longer, never past the cap"


def test_a_transcript_whose_only_change_is_its_images_is_not_called_condensed() -> None:
    """Nothing was cut to fit: the extractor is not told the transcript is partial."""
    pasted = MessageEnd(role="user", content=[{"type": "text", "text": "look"}, {"type": "image", "data": "B" * 10}])

    note = condense(run_items([env(1, pasted)]), FIRST).stats.prompt_note() or ""

    assert "images omitted" in note and "condensed" not in note and "removed" not in note


def test_the_provenance_reply_is_the_final_answer_not_narration_between_calls() -> None:
    narrating = MessageEnd(role="assistant", content=[
        {"type": "text", "text": "Let me check that file."},
        {"type": "tool_call", "id": "c1", "name": "read", "arguments": {}},
    ])
    answer = MessageEnd(role="assistant", content=[{"type": "text", "text": "It builds with make check."}])
    asked = MessageEnd(role="user", content=[{"type": "text", "text": "how does it build?"}])

    finished = turn._said([env(1, asked), env(2, answer), env(3, narrating)])
    stopped = turn._said([env(1, asked), env(2, narrating), env(3, TurnEnd(stop_reason="aborted"))])

    assert finished.reply == "It builds with make check.", "the final word, even with narration after it"
    assert stopped.reply == "Let me check that file.", "a stopped run has only its narration"
