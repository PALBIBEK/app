"""Supervisor tool-invoke outbox — exactly-once completion (ADR-004)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.agent.factory import create_runtime
from supervisor.catalog.store import CompositionCatalog
from supervisor.seams.types import ToolResult
from supervisor.session.tool_outbox import idempotency_key
from tests.unit.test_catalog_pins import _offer_composition


@pytest.mark.asyncio
async def test_invoke_tool_exactly_once_same_call_id(session_env) -> None:
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    seams = m._seams_for_session(sid, await m._require_active(sid))
    assert seams is not None
    real_invoke = seams.tool_provider.invoke
    calls: list[tuple[str, dict]] = []

    def counting_invoke(ctx, name, arguments):  # type: ignore[no-untyped-def]
        calls.append((name, dict(arguments)))
        return real_invoke(ctx, name, arguments)

    seams.tool_provider.invoke = counting_invoke  # type: ignore[method-assign]

    first = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="echo",
        arguments={"text": "once"},
        tool_call_id="tc-dup",
    )
    second = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="echo",
        arguments={"text": "once"},
        tool_call_id="tc-dup",
    )
    assert first.result.ok and second.result.ok
    assert first.result.payload == second.result.payload == {"text": "once"}
    assert len(calls) == 1

    events = await session_env["reader"].read(sid, since=-1)
    assert sum(1 for e in events if isinstance(e.payload, ToolStart)) == 1
    assert sum(1 for e in events if isinstance(e.payload, ToolEnd)) == 1
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_outbox_pending_then_complete_is_idempotent(session_env) -> None:
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    outbox = m._tool_outbox
    begin = await outbox.begin(
        sid,
        tool_call_id="tc-pend",
        tool_name="echo",
        arguments={"text": "mid"},
        trace_id=None,
    )
    assert begin.action == "execute"
    row = await outbox.get_by_key(idempotency_key(sid, "tc-pend"))
    assert row is not None and row.status == "pending"

    result = ToolResult(ok=True, payload={"text": "mid"})
    await outbox.complete(
        sid,
        tool_call_id="tc-pend",
        tool_name="echo",
        result=result,
        trace_id=None,
    )
    # Second complete returns cached; no second ToolEnd
    again = await outbox.complete(
        sid,
        tool_call_id="tc-pend",
        tool_name="echo",
        result=ToolResult(ok=True, payload={"text": "other"}),
        trace_id=None,
    )
    assert again.payload == {"text": "mid"}

    events = await session_env["reader"].read(sid, since=-1)
    assert sum(1 for e in events if isinstance(e.payload, ToolEnd)) == 1
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_outbox_begin_after_done_returns_cached(session_env) -> None:
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    await m.invoke_tool(
        sid, tenant_id="t1", name="echo", arguments={"text": "x"}, tool_call_id="tc-c"
    )
    begin = await m._tool_outbox.begin(
        sid,
        tool_call_id="tc-c",
        tool_name="echo",
        arguments={"text": "x"},
        trace_id=None,
    )
    assert begin.action == "cached"
    assert begin.cached is not None
    assert begin.cached.payload == {"text": "x"}
    await m.terminate(sid)
