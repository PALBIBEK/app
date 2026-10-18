"""D7 §5 B — MemoryProfile pin: default vs recall-enabled; null fails loud."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.catalog.store import memory_profile_from_dict
from supervisor.catalog.types import MemoryProfile
from supervisor.seams.factory import build_context_memory_from_profile
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider
from tests.d7_acceptance.conftest import HARNESS_PROFILES, offered_memory_profiles


def test_default_profile_compaction_on_recall_off(default_memory_profile: MemoryProfile) -> None:
    """§5 B / shipped default: compaction enabled; recall disabled (backend none)."""
    p = default_memory_profile
    assert p.compaction_enabled is True
    assert p.recall_enabled is False
    assert p.recall_backend == "none"


def test_b_no_profile_builds_null_rpc_unconfigured() -> None:
    """§5 B: no MemoryProfile pin → Null provider with rpc_configured=False (fail loud)."""
    gateway = FakeModelGatewayClient()
    memory = build_context_memory_from_profile(None, gateway)
    assert isinstance(memory, NullContextMemoryProvider)
    assert getattr(memory, "rpc_configured", True) is False


def test_b_default_profile_builds_compaction_provider(
    default_memory_profile: MemoryProfile,
) -> None:
    """Pinned default (compaction on) must not be Null/unconfigured."""
    gateway = FakeModelGatewayClient()
    memory = build_context_memory_from_profile(default_memory_profile, gateway)
    assert not isinstance(memory, NullContextMemoryProvider) or getattr(
        memory, "rpc_configured", False
    )
    # Compaction path must be live (BasicCompaction or Composite wrapping it).
    from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
    from supervisor.seams.providers.composite_context_memory import (
        CompositeContextMemoryProvider,
    )

    assert isinstance(memory, (BasicCompactionProvider, CompositeContextMemoryProvider))


def test_b_recall_enabled_profile_shipped_for_staging() -> None:
    """§5 B / C / T7: at least one offered profile with recall.enabled for durable inject.

    Default stays conservative; staging/demo needs an explicit recall-on profile
    (or ToolSet path). Missing profile is an intentional D7 red.
    """
    profiles = offered_memory_profiles()
    assert profiles, f"expected MemoryProfile JSON under {HARNESS_PROFILES}"
    recall_on = [
        p
        for p in profiles
        if bool((p.get("recall") or {}).get("enabled"))
        and (p.get("recall") or {}).get("backend") not in (None, "none", "")
    ]
    assert recall_on, (
        "D7: need a shipped MemoryProfile with recall.enabled + non-none backend "
        "(durable / githi_markdown / mcp) — default alone is insufficient for turn R/W"
    )


def test_b_factory_supports_durable_backed_recall() -> None:
    """§5 C / T7: factory must map a durable (or memory-service) recall backend.

    Markdown/MCP alone do not close the durable Tier 2 north star.
    """
    factory_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "seams"
        / "factory.py"
    ).read_text(encoding="utf-8")
    # Accept common naming: durable, memory_service, tier2, DurableRecall
    durable_markers = (
        "durable",
        "memory_service",
        "DurableRecall",
        "tier2",
        "MEMORY_SERVICE",
    )
    assert any(m in factory_src for m in durable_markers), (
        "build_context_memory_from_profile must wire a durable-memory recall backend "
        "(not only githi_markdown / mcp)"
    )


def test_default_profile_json_matches_contract_knobs() -> None:
    """Pin source of truth: harness/memory_profiles/default.json recall off."""
    raw = json.loads((HARNESS_PROFILES / "default.json").read_text(encoding="utf-8"))
    assert raw["compaction"]["enabled"] is True
    assert raw["recall"]["enabled"] is False
    assert raw["recall"]["backend"] == "none"
    # Round-trip through catalog loader.
    art = memory_profile_from_dict(raw)
    assert art.name == "default"
    assert art.recall_enabled is False
