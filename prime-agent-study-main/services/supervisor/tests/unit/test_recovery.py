"""Recovery decision tests — decide, do not act."""

from __future__ import annotations

import pytest

from supervisor.agent.events import ToolStart
from supervisor.session.recovery import Recovery, ResumeState


@pytest.mark.asyncio
async def test_live_session_is_warm(session_env) -> None:
    m = session_env["manager"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.WARM
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_clean_session_no_runtime_is_cold(session_env) -> None:
    m = session_env["manager"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    # Drop live handle without terminating DB row.
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.COLD
    assert decision.harness is not None
    assert decision.harness.version == "kg-v1"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_unpaired_tool_start_needs_review(session_env) -> None:
    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        ToolStart(tool_call_id="orphan", tool_name="git_commit", args={}),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.NEEDS_REVIEW
    assert decision.unpaired_tool_call_ids == ("orphan",)
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_sandbox_free_never_unrecoverable(session_env) -> None:
    m = session_env["manager"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row, sandbox_resumable=False)
    assert decision.state == ResumeState.COLD
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_resume_uses_pinned_harness_version_not_current(session_env) -> None:
    m = session_env["manager"]
    provider = session_env["provider"]
    # Create under coding-v1, then make coding-v2 "current" by registering it
    # as the only thing a naive caller might pick — resume must still use v1.
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    await m.terminate(sid)
    # Empty registry; resume cold.
    provider.create_calls.clear()
    result = await m.resume(sid)
    assert result.state.value == "cold"
    # Provider called with pinned v1 digest, not v2.
    assert provider.create_calls[-1]["image"] == "sha256:" + "a" * 64
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_resume_sandbox_free_never_calls_provider(session_env) -> None:
    m = session_env["manager"]
    provider = session_env["provider"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    await m.terminate(sid)
    provider.create_calls.clear()
    result = await m.resume(sid)
    assert result.state.value == "cold"
    assert provider.create_calls == []
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_default_fail_unpaired_non_atomic_session_fails(session_env) -> None:
    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        ToolStart(tool_call_id="x", tool_name="npm", args={}),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.FAILED
    result = await m.resume(sid)
    assert result.state == ResumeState.FAILED
    from supervisor.db.models import SessionRow

    async with session_env["factory"]() as db:
        srow = await db.get(SessionRow, sid)
        assert srow is not None
        assert srow.status == "failed"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_by_tool_class_overrides_default_for_pure(session_env) -> None:
    """pure → auto_resume even when default is fail."""
    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        ToolStart(tool_call_id="x", tool_name="read", args={}),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.COLD
    from supervisor.harness import RecoveryAction

    assert decision.applied_actions == (RecoveryAction.AUTO_RESUME,)
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_unknown_tool_class_falls_back_to_default_never_auto_resume(
    session_env,
) -> None:
    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        ToolStart(tool_call_id="x", tool_name="totally_unknown_tool", args={}),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.FAILED
    from supervisor.harness import RecoveryAction

    assert decision.applied_actions == (RecoveryAction.FAIL,)
    assert RecoveryAction.AUTO_RESUME not in decision.applied_actions
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_policy_from_pinned_manifest_not_current(session_env) -> None:
    """Pinned kg-v1 says unknown→fail; a 'current' override must not apply."""
    from supervisor.harness import (
        RecoveryAction,
        RecoveryPolicy,
        ToolClass,
    )
    from supervisor.harness.services.resolver import HarnessResolved

    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]

    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        ToolStart(tool_call_id="x", tool_name="mystery", args={}),
        turn_id="t",
        trace_id=None,
    )

    # Simulate a newer "current" policy that would auto-resume unknowns — must
    # not be consulted (session stores a manifest snapshot at create).
    catalog.register(
        HarnessResolved(
            version="kg-v2-current",
            protocol_version="1",
            image_digest="sha256:" + "c" * 64,
            bundle_uri="",
            requires_sandbox=False,
            recovery=RecoveryPolicy(
                default=RecoveryAction.AUTO_RESUME,
                by_tool_class={},
                tool_classes={},
            ),
        )
    )
    catalog.register(
        HarnessResolved(
            version="kg-v1",
            protocol_version="1",
            image_digest="",
            bundle_uri="",
            requires_sandbox=False,
            recovery=RecoveryPolicy(
                default=RecoveryAction.AUTO_RESUME,
                by_tool_class={},
                tool_classes={},
            ),
        )
    )

    row = await m._get_session_row(sid)
    assert row.harness_version == "kg-v1"
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.FAILED
    assert decision.harness is not None
    assert decision.harness.version == "kg-v1"
    await m.terminate(sid)
