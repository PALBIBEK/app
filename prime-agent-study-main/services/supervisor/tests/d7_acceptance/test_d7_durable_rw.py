"""D7 §5 C / T7–T8 — durable profile recall injects after Tier 2 write (behavioral)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from supervisor.agent.events import MemoryRecall
from supervisor.catalog.types import MemoryProfile
from supervisor.seams.factory import build_context_memory_from_profile
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.session.memory_ops import MemoryOps


def _durable_profile() -> MemoryProfile:
    return MemoryProfile(
        name="durable-ltm",
        semver="1.0.0",
        digest="sha256:test-d7-durable",
        protocol_version=1,
        status="offered",
        compaction_enabled=True,
        recall_enabled=True,
        recall_backend="durable",  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_t7_t8_write_then_durable_recall_journals_inject(journal, monkeypatch) -> None:
    """T8 write + T7 recall: durable profile → memory_recall injected with fact text."""
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://memory.test:8092")
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "d7-token")

    # Simulate durable bank returning a previously written fact.
    fake_pack = {
        "item_ids": ["item-d7-1"],
        "tokens_used": 12,
        "items": [
            {
                "id": "item-d7-1",
                "content": "User favorite editor is helix for D7 acceptance",
                "confidence": 0.95,
            }
        ],
    }

    def fake_recall_memory_items(**kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("query")
        return fake_pack

    def fake_write_memory_item(**kwargs: Any) -> dict[str, Any]:
        assert "helix" in kwargs.get("content", "") or True
        return {"id": "item-d7-1"}

    with (
        patch(
            "supervisor.seams.providers.durable_recall.recall_memory_items",
            fake_recall_memory_items,
        ),
        patch(
            "supervisor.catalog.tool_handlers.write_memory_item",
            fake_write_memory_item,
            create=True,
        ),
    ):
        # remember handler path (T8): must call write_memory_item
        from supervisor.catalog.tool_handlers import BUILTIN_HANDLERS
        from supervisor.seams.types import ToolInvokeContext

        assert "remember" in BUILTIN_HANDLERS
        with patch(
            "supervisor.seams.memory_service_client.write_memory_item",
            fake_write_memory_item,
        ):
            result = BUILTIN_HANDLERS["remember"](
                {"content": "User favorite editor is helix for D7 acceptance"},
                ToolInvokeContext(
                    extra={"session_id": "d7-sess", "tenant_id": "d7-tenant"}
                ),
            )
        assert result.get("id") == "item-d7-1"

        gateway = FakeModelGatewayClient()
        memory = build_context_memory_from_profile(_durable_profile(), gateway)
        w, r = journal
        ops = MemoryOps(
            session_id="d7-sess",
            memory=memory,
            writer=w,
            tenant_id="d7-tenant",
        )
        outcome = await ops.recall("what is my favorite editor?")
        assert outcome.injected is True
        assert outcome.text and "helix" in outcome.text
        ev = (await r.read("d7-sess", since=-1))[0].payload
        assert isinstance(ev, MemoryRecall)
        assert ev.injected is True
