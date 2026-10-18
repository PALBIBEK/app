"""Phase 0.5 — ContextMemory binder maps MemoryProfile → non-Null provider."""

from __future__ import annotations

from pathlib import Path

from supervisor.catalog.binder import bind_runtime_seams
from supervisor.catalog.resolve import resolve_composition
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import (
    AgentComposition,
    DigestRef,
    MemoryProfile,
)
from supervisor.seams.factory import build_context_memory_from_profile
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.composite_context_memory import (
    CompositeContextMemoryProvider,
)
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.markdown_recall import MarkdownRecallProvider
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider
from tests.unit.test_catalog_pins import _offer_composition, _sample_prompt_pack, _sample_tool_set


def _sample_memory_profile(
    *,
    name: str = "default",
    compaction_enabled: bool = True,
    recall_enabled: bool = False,
    recall_backend: str = "none",
) -> MemoryProfile:
    return MemoryProfile(
        name=name,
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        compaction_enabled=compaction_enabled,
        summarization_model="cheap-summary",
        recall_enabled=recall_enabled,
        recall_backend=recall_backend,  # type: ignore[arg-type]
    )


def test_build_context_memory_null_without_profile() -> None:
    mem = build_context_memory_from_profile(None, FakeModelGatewayClient())
    assert isinstance(mem, NullContextMemoryProvider)


def test_build_context_memory_basic_when_compaction_enabled() -> None:
    profile = _sample_memory_profile(compaction_enabled=True)
    mem = build_context_memory_from_profile(profile, FakeModelGatewayClient())
    assert isinstance(mem, BasicCompactionProvider)


def test_build_context_memory_composite_with_markdown_recall() -> None:
    profile = _sample_memory_profile(
        compaction_enabled=True,
        recall_enabled=True,
        recall_backend="githi_markdown",
    )
    mem = build_context_memory_from_profile(profile, FakeModelGatewayClient())
    assert isinstance(mem, CompositeContextMemoryProvider)
    assert isinstance(mem._recall, MarkdownRecallProvider)  # noqa: SLF001


def test_bind_runtime_seams_null_without_memory_pin() -> None:
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    assert isinstance(seams.context_memory, NullContextMemoryProvider)


def test_bind_runtime_seams_non_null_with_memory_pin() -> None:
    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    profile = cat.offer(_sample_memory_profile())
    comp = cat.offer(
        AgentComposition(
            name="coding-with-memory",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            memory_profile=DigestRef(id=profile.name, digest=profile.digest),
        )
    )
    pins = resolve_composition(cat, composition_id=comp.name)
    assert pins.memory_profile_id == profile.name
    seams = bind_runtime_seams(pins, cat)
    assert isinstance(seams.context_memory, BasicCompactionProvider)
    assert not isinstance(seams.context_memory, NullContextMemoryProvider)


def test_shipped_coding_v1_pins_default_memory_profile() -> None:
    from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir

    catalog = bootstrap_composition_catalog(default_catalog_dir())
    pins = resolve_composition(catalog, composition_id="coding-v1")
    assert pins.memory_profile_id == "default"
    seams = bind_runtime_seams(pins, catalog)
    assert isinstance(seams.context_memory, BasicCompactionProvider)


def test_srp_leak_no_compaction_concretes_outside_binder_factory() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = (
        "BasicCompactionProvider",
        "MarkdownRecallProvider",
        "McpSemanticRecallProvider",
        "CompositeContextMemoryProvider",
        "build_context_memory_from_profile",
    )
    for pkg in ("journal", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"
    for path in (src / "session").rglob("*.py"):
        if path.name == "memory_ops.py":
            continue
        text = path.read_text()
        for needle in banned:
            assert needle not in text, f"{path}: {needle}"
