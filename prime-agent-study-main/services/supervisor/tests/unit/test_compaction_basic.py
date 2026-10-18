"""compaction-basic — orphan-safe trim + gateway summarizer (ported)."""

from __future__ import annotations

import pytest

from supervisor.seams.compaction import (
    SUMMARY_MARKER,
    build_summary_request,
    compact,
    estimate_tokens,
    extract_previous_summary,
    needs_compaction,
    summarize_span,
    whole_exchanges,
)
from supervisor.seams.context_memory import CompactionPreparation
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.context_memory import MemoryInvokeContext


def _pair(i: int, size: int = 100) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"c{i}",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "content": "y" * size},
    ]


def _transcript(pairs: int) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "do the thing"}]
    for i in range(pairs):
        msgs += _pair(i)
    return msgs


class RecordingSummarizer:
    def __init__(self, text: str = "## Goal\nfind the commit\n\n## Progress\n- [x] cloned"):
        self.text = text
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict], previous_summary: str | None = None) -> str:
        self.calls.append(list(messages))
        self.previous = previous_summary
        return self.text


class FailingSummarizer:
    def __call__(self, messages: list[dict], previous_summary: str | None = None) -> str:
        raise RuntimeError("provider 503")


def test_cb_estimate_grows_with_content() -> None:
    assert estimate_tokens([{"role": "user", "content": "x" * 400}]) > estimate_tokens(
        [{"role": "user", "content": "x"}]
    )


def test_cb_needs_compaction_threshold() -> None:
    assert needs_compaction(_transcript(1), limit_tokens=100_000) is False
    assert needs_compaction(_transcript(200), limit_tokens=1_000) is True


def test_cb_compact_reduces_size() -> None:
    msgs = _transcript(40)
    out = compact(msgs, limit_tokens=1_000)
    assert estimate_tokens(out) < estimate_tokens(msgs)


def test_cb_keeps_first_user_and_latest() -> None:
    msgs = _transcript(40)
    msgs.append({"role": "user", "content": "LATEST"})
    out = compact(msgs, limit_tokens=500)
    assert any(m.get("content") == "do the thing" for m in out if m.get("role") == "user")
    assert out[-1]["content"] == "LATEST"


def test_cb_never_orphans_tool_result() -> None:
    out = compact(_transcript(40), limit_tokens=500)
    call_ids = {
        c["id"]
        for m in out
        if m.get("role") == "assistant"
        for c in (m.get("tool_calls") or [])
    }
    for m in out:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids


def test_cb_never_orphans_tool_call() -> None:
    out = compact(_transcript(40), limit_tokens=500)
    result_ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    for m in out:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for call in m["tool_calls"]:
                assert call["id"] in result_ids


def test_cb_whole_exchanges_extends_past_leading_tool() -> None:
    """Split-turn boundary: suffix starting on tool pulls assistant above it."""
    full = [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
        {"role": "user", "content": "latest"},
    ]
    kept = whole_exchanges(full[-2:], full)
    assert kept[0]["role"] == "assistant"
    assert kept[-1]["content"] == "latest"


def test_cb_whole_exchanges_drops_trailing_orphan_assistant() -> None:
    full = [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "done"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read"}}],
        },
    ]
    kept = whole_exchanges(full[-2:], full)
    assert kept[-1]["role"] == "tool"
    assert "c2" not in {
        c["id"] for m in kept if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])
    }


def test_cb_summarize_span_uses_marker() -> None:
    text = summarize_span(
        [{"role": "user", "content": "hi"}],
        summarizer=RecordingSummarizer(),
    )
    assert text.startswith(SUMMARY_MARKER)
    assert "cloned" in text


def test_cb_summarize_span_degrades_on_failure() -> None:
    text = summarize_span(
        [{"role": "user", "content": "hi"}],
        summarizer=FailingSummarizer(),
    )
    assert "compacted" in text.lower()


def test_cb_build_summary_request_has_sections() -> None:
    request = build_summary_request([{"role": "user", "content": "hi"}])
    blob = " ".join(str(m.get("content", "")) for m in request)
    for section in ("## Goal", "## Progress", "## Next Steps"):
        assert section in blob


def test_cb_extract_previous_summary() -> None:
    marked = compact(
        _transcript(40),
        limit_tokens=500,
        summarizer=RecordingSummarizer(),
    )
    assert extract_previous_summary(marked) is not None


def test_cb_basic_provider_returns_custom_summary() -> None:
    gateway = FakeModelGatewayClient(assistant_text="## Goal\ntask done")
    provider = BasicCompactionProvider(gateway)
    prep = CompactionPreparation(
        first_kept_entry_id="e99",
        messages_to_summarize=[{"role": "user", "content": "long history"}],
        tokens_before=90_000,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    decision = provider.before_compact(
        MemoryInvokeContext(session_id="s1"),
        prep,
        reason="threshold",
    )
    assert decision.action == "custom"
    assert decision.result is not None
    assert decision.result.summary.startswith(SUMMARY_MARKER)
    assert "task done" in decision.result.summary
    assert decision.result.first_kept_entry_id == "e99"
    assert decision.result.tokens_before == 90_000


def test_cb_basic_provider_empty_span_cancels() -> None:
    provider = BasicCompactionProvider(FakeModelGatewayClient())
    prep = CompactionPreparation(
        first_kept_entry_id="e1",
        messages_to_summarize=[],
        tokens_before=0,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    decision = provider.before_compact(
        MemoryInvokeContext(session_id="s2"),
        prep,
        reason="manual",
    )
    assert decision.action == "cancel"
