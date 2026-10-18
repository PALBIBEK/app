"""D7 P0 — durable ContextMemory recall + remember tool HTTP write."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from supervisor.catalog.tool_handlers import invoke_builtin
from supervisor.catalog.types import MemoryProfile
from supervisor.seams.context_memory import MemoryInvokeContext
from supervisor.seams.factory import build_context_memory_from_profile
from supervisor.seams.providers.composite_context_memory import (
    CompositeContextMemoryProvider,
)
from supervisor.seams.providers.durable_recall import DurableRecallProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.types import ToolInvokeContext


def _durable_profile() -> MemoryProfile:
    return MemoryProfile(
        name="durable-ltm",
        semver="1.0.0",
        digest="sha256:" + "a" * 64,
        protocol_version=1,
        status="offered",
        compaction_enabled=True,
        summarization_model="cheap-summary",
        recall_enabled=True,
        recall_backend="durable",
    )


def test_factory_wires_durable_recall_backend() -> None:
    mem = build_context_memory_from_profile(
        _durable_profile(), FakeModelGatewayClient()
    )
    assert isinstance(mem, CompositeContextMemoryProvider)
    assert isinstance(mem._recall, DurableRecallProvider)  # noqa: SLF001


def test_factory_wires_memory_service_alias() -> None:
    profile = MemoryProfile(
        name="alias",
        semver="1.0.0",
        digest="sha256:" + "b" * 64,
        protocol_version=1,
        status="offered",
        recall_enabled=True,
        recall_backend="memory_service",  # type: ignore[arg-type]
    )
    mem = build_context_memory_from_profile(profile, FakeModelGatewayClient())
    assert isinstance(mem, CompositeContextMemoryProvider)
    assert isinstance(mem._recall, DurableRecallProvider)  # noqa: SLF001


def test_durable_recall_formats_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://memory.test:8091")
    provider = DurableRecallProvider(base_url="http://memory.test:8091")

    def _fake_recall(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["query"] == "dark mode"
        assert kwargs["scope"]["tenant"] == "t1"
        return {
            "item_ids": ["i1"],
            "tokens_used": 8,
            "items": [{"id": "i1", "content": "prefers dark mode", "confidence": 0.9}],
        }

    with patch(
        "supervisor.seams.providers.durable_recall.recall_memory_items",
        side_effect=_fake_recall,
    ):
        block = provider.recall_for_turn(
            MemoryInvokeContext(session_id="s1", tenant_id="t1"),
            user_prompt="dark mode",
        )
    assert block is not None
    assert "dark mode" in block.text
    assert block.source == "semantic"


def test_durable_recall_degrades_without_url() -> None:
    provider = DurableRecallProvider(base_url="")
    assert (
        provider.recall_for_turn(
            MemoryInvokeContext(session_id="s1"),
            user_prompt="hello",
        )
        is None
    )


def test_durable_recall_degrades_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DurableRecallProvider(base_url="http://memory.test:8091")
    with patch(
        "supervisor.seams.providers.durable_recall.recall_memory_items",
        side_effect=RuntimeError("down"),
    ):
        assert (
            provider.recall_for_turn(
                MemoryInvokeContext(session_id="s1", tenant_id="t1"),
                user_prompt="hello",
            )
            is None
        )


def test_remember_posts_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://memory.test:8091")
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "tok")

    captured: dict = {}

    def _fake_write(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"id": "item-42"}

    with patch(
        "supervisor.seams.memory_service_client.write_memory_item",
        side_effect=_fake_write,
    ):
        result = invoke_builtin(
            "remember",
            {"content": "team uses postgres"},
            ToolInvokeContext(
                extra={
                    "session_id": "sess-9",
                    "tenant_id": "tenant-a",
                    "memory_subject": "user:42",
                    "memory_namespace": "facts",
                }
            ),
        )
    assert result["id"] == "item-42"
    assert result["content"] == "team uses postgres"
    assert result["scope"]["tenant"] == "tenant-a"
    assert result["scope"]["subject"] == "user:42"
    assert captured["content"] == "team uses postgres"
    assert captured["scope"]["namespace"] == "facts"


def test_remember_requires_content() -> None:
    with pytest.raises(ValueError, match="content"):
        invoke_builtin("remember", {}, ToolInvokeContext())


def test_shipped_durable_ltm_profile_loads() -> None:
    from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir

    catalog = bootstrap_composition_catalog(default_catalog_dir())
    arts = [
        a
        for a in catalog.list_artifacts("memory_profile", status="offered")
        if a.name == "durable-ltm"
    ]
    assert arts, "expected offered durable-ltm MemoryProfile"
    assert arts[0].recall_enabled is True
    assert arts[0].recall_backend == "durable"


def test_shipped_coding_durable_composition_pins_durable_ltm() -> None:
    from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
    from supervisor.catalog.resolve import resolve_composition

    catalog = bootstrap_composition_catalog(default_catalog_dir())
    pins = resolve_composition(catalog, composition_id="coding-durable-v1")
    assert pins.memory_profile_id == "durable-ltm"
    assert pins.tool_set_id == "coding-tools"


def test_srp_leak_no_durable_recall_outside_factory() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = ("DurableRecallProvider",)
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
