"""context-memory-seam — ContextMemoryProvider protocol + RuntimeSeams wire."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.seams import RuntimeSeams
from supervisor.seams import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
)
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider


def _prep() -> CompactionPreparation:
    return CompactionPreparation(
        first_kept_entry_id="e1",
        messages_to_summarize=[{"role": "user", "content": "hello"}],
        tokens_before=10_000,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )


def test_cm_u1_null_provider_proceed_default() -> None:
    provider = NullContextMemoryProvider()
    ctx = MemoryInvokeContext(session_id="s1")
    decision = provider.before_compact(ctx, _prep(), reason="threshold")
    assert decision == CompactionDecision(action="proceed_default")
    assert provider.recall_for_turn(ctx, user_prompt="hi") is None
    assert provider.estimate_tokens([{"role": "user", "content": "x"}]) >= 0


def test_cm_u2_basic_compaction_returns_custom_summary() -> None:
    provider = BasicCompactionProvider(FakeModelGatewayClient(assistant_text="## Goal\nok"))
    decision = provider.before_compact(
        MemoryInvokeContext(session_id="s2"),
        _prep(),
        reason="manual",
    )
    assert decision.action == "custom"
    assert decision.result is not None
    assert "ok" in decision.result.summary


def test_cm_u3_build_runtime_seams_includes_context_memory() -> None:
    seams = build_runtime_seams()
    assert isinstance(seams, RuntimeSeams)
    assert isinstance(seams.context_memory, ContextMemoryProvider)
    assert isinstance(seams.context_memory, NullContextMemoryProvider)


def test_cm_u4_override_context_memory() -> None:
    custom = BasicCompactionProvider(FakeModelGatewayClient())
    seams = build_runtime_seams(context_memory=custom)
    assert seams.context_memory is custom


def test_cm_u5_append_plug_state_returns_id() -> None:
    provider = NullContextMemoryProvider()
    entry_id = provider.append_plug_state(
        MemoryInvokeContext(session_id="s3"),
        "my_plug",
        {"k": "v"},
    )
    assert entry_id.startswith("plug-")


def test_cm_c1_protocol_module_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    assert (root / "contracts" / "memory.md").is_file()
    seam = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "seams"
        / "context_memory.py"
    )
    assert seam.is_file()
    assert "class ContextMemoryProvider" in seam.read_text()


def test_cm_a1_memory_subagent_doc_not_session_memory() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "seams"
        / "providers"
        / "memory_subagent.py"
    )
    body = path.read_text()
    assert "NOT session memory" in body or "Not related to" in body
    assert "ContextMemoryProvider" in body


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda: NullContextMemoryProvider(),
        lambda: BasicCompactionProvider(FakeModelGatewayClient()),
    ],
)
def test_cm_u6_providers_satisfy_protocol(provider_factory) -> None:
    assert isinstance(provider_factory(), ContextMemoryProvider)
