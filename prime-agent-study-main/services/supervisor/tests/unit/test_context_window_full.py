"""A full context window is said, not answered with silence; compaction sees the whole prompt.

Found with a real model on a 14k-token route: once the conversation filled the
window, every call went out capped to one output token, came back empty with
``length``, and the turn ended as if the model had answered nothing. Compaction
never ran, because its estimate counted the conversation but not the system
prompt, the tool schemas and recalled memory the model also receives.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from supervisor.agent.events import Envelope, MessageEnd, Usage
from supervisor.models.messages import (
    Context,
    ContextWindowFull,
    UserMessage,
    clamp_max_tokens,
    fits_with_reply,
    safety_tokens,
)
from supervisor.models.spec import ModelSpec, Pricing
from supervisor.temporal.context_memory import _reported_tokens
from supervisor.temporal.contracts import NON_RETRYABLE


def _spec(window: int) -> ModelSpec:
    return ModelSpec(
        id="or-free-small", name="small", provider="openrouter", api="openai-completions",  # type: ignore[arg-type]
        base_url="http://gateway", context_window=window, max_tokens=2000, pricing=Pricing(input=0, output=0, cache_read=0, cache_write=0),
    )


def _context(chars: int) -> Context:
    return Context(system_prompt="", messages=(UserMessage("x" * chars),))


def test_room_for_an_answer_is_given_up_to_the_cap() -> None:
    assert clamp_max_tokens(_spec(200_000), _context(400), 2000) == 2000


def test_a_window_nearly_full_still_answers_with_what_is_left() -> None:
    spec = _spec(14_000)
    budget = clamp_max_tokens(spec, _context(4 * 8_000), 2000)
    assert 1024 <= budget <= 2000


def test_a_full_window_refuses_the_call_instead_of_capping_it_to_nothing() -> None:
    spec = _spec(14_000)
    with pytest.raises(ContextWindowFull) as raised:
        clamp_max_tokens(spec, _context(4 * (14_000 - safety_tokens(14_000))), 2000)
    assert raised.value.window == 14_000 and "no room is left for an answer" in str(raised.value)
    assert "ContextWindowFull" in NON_RETRYABLE  # a retry would send the same prompt


class _Chars:
    """A context-memory estimate: four characters a token."""

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return len(json.dumps(messages)) // 4


def _env(seq: int, payload: Any) -> Envelope:
    return Envelope(session_id="s", seq=seq, event_id=str(seq), ts_ms=0, turn_id="r", trace_id=None, payload=payload)


def test_compaction_counts_what_the_provider_counted_plus_what_came_since() -> None:
    said = [{"type": "text", "text": "y" * 4000}]
    events = [
        _env(1, MessageEnd(role="user", content=[{"type": "text", "text": "hi"}])),
        _env(2, MessageEnd(role="assistant", content=[{"type": "text", "text": "ok"}], usage=Usage(9_000, 200))),
        _env(3, MessageEnd(role="user", content=said)),
    ]
    reported = _reported_tokens(events, _Chars())  # type: ignore[arg-type]
    assert reported == 9_200 + _Chars().estimate_tokens([{"role": "user", "content": said}])
    assert _reported_tokens(events[:1], _Chars()) == 0  # type: ignore[arg-type]  # nothing reported yet


def test_the_safety_margin_scales_down_for_small_windows() -> None:
    assert safety_tokens(200_000) == 4096 and safety_tokens(14_000) == 1750


def test_compaction_starts_before_a_call_would_be_refused() -> None:
    from supervisor.temporal.context_memory import _Binding

    profile = type("P", (), {"reserve_tokens": 16384, "keep_recent_tokens": 20000, "pressure_ratio": 0.8})()
    binding = _Binding(profile, None, None, gateway=None, model=None)  # type: ignore[arg-type]
    for window in (3_000, 14_000, 32_000, 200_000):
        assert binding.limit(window) <= fits_with_reply(window), window


def test_a_span_too_big_for_one_summary_is_summarised_in_pieces_that_fit() -> None:
    from supervisor.seams.compaction.core import SUMMARY_MARKER, summarize_span

    calls: list[int] = []

    def summarizer(messages: list[dict[str, Any]], previous: str | None) -> str:
        size = len(json.dumps(messages)) + len(previous or "")
        calls.append(size)
        if size > 3_000:  # what this summarizer's window holds
            raise ContextWindowFull(prompt_tokens=size // 4, window=750, model="small")
        return f"{previous or ''} +{len(messages)}"

    span = [{"role": "user", "content": "a" * 1_000}, {"role": "assistant", "content": "b" * 1_000},
            {"role": "user", "content": "c" * 8_000}]  # the last one alone is too big
    summary = summarize_span(span, summarizer=summarizer)
    assert summary.startswith(SUMMARY_MARKER) and "+" in summary, summary  # a summary, not the bare notice
    assert max(c for c in calls if c <= 3_000) and calls[-1] <= 3_000  # it ended on a call that fit
