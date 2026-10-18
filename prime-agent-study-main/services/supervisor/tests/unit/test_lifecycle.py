"""Phase 1 — idle reap, drain status, and lifecycle wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.session.lifecycle import reap_idle
from supervisor.session.modes import SessionMode
from supervisor.sandbox.base import SnapshotUnsupported
from tests.support.session_env import _DummySandbox, make_catalog, make_manifest
from tests.unit.test_sandbox_wiring import drain_for_deploy


class PausingSandbox(_DummySandbox):
    def __init__(self) -> None:
        super().__init__()
        self.pause_calls = 0
        self.destroyed = False

    async def pause(self) -> str:
        self.pause_calls += 1
        return "snap-test"

    async def destroy(self) -> None:
        self.destroyed = True


class PausingProvider:
    name = "pausing"

    def __init__(self) -> None:
        self.sandboxes: list[PausingSandbox] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        sbx = PausingSandbox()
        self.sandboxes.append(sbx)
        return sbx

    async def resume(self, snapshot_id: str) -> PausingSandbox:
        return PausingSandbox()

    async def get(self, sandbox_id: str) -> PausingSandbox | None:
        return None


@pytest.mark.asyncio
async def test_reap_idle_skips_busy_session(session_env) -> None:
    from supervisor.agent.base import AgentState

    class UnsettledRuntime:
        async def get_state(self) -> AgentState:
            return AgentState(
                session_id="busy",
                is_busy=True,
                is_compacting=False,
                pending_count=1,
                message_count=0,
                model=None,
                context_window=None,
            )

    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    session_env["registry"].put(sid, UnsettledRuntime())

    reaped = await reap_idle(m, max_idle_seconds=1.0)
    assert sid not in reaped
    assert session_env["registry"].get(sid) is not None


@pytest.mark.asyncio
async def test_reap_idle_pauses_sticky_idle_session(session_env) -> None:
    provider = PausingProvider()
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry

    factory = session_env["factory"]
    writer = JournalWriter(factory, blob_dir=session_env["writer"]._blob_dir)  # noqa: SLF001
    reader = JournalReader(factory, blob_dir=session_env["reader"]._blob_dir)  # noqa: SLF001
    registry = SessionRegistry()
    catalog = make_catalog()
    m = SessionManager(
        session_factory=factory,
        writer=writer,
        reader=reader,
        registry=registry,
        catalog=catalog,
        sandbox_provider=provider,
        live_queue_max=8,
    )
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")

    async with factory() as db:
        idx = await db.get(SessionIndexRow, sid)
        assert idx is not None
        idx.last_event_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await db.commit()

    reaped = await reap_idle(m, max_idle_seconds=60.0)
    assert sid in reaped
    assert registry.get(sid) is None
    assert m._modes.get(sid) == SessionMode.STICKY  # noqa: SLF001
    sbx = m._sandboxes.get(sid)  # noqa: SLF001
    assert sbx is not None
    assert sbx.pause_calls == 1
    assert not sbx.destroyed

    async with factory() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.status == "paused"


@pytest.mark.asyncio
async def test_reap_idle_terminates_idle_stateless_session(session_env) -> None:
    catalog = session_env["catalog"]
    catalog.register(
        make_manifest(
            "stateless-v1",
            requires_sandbox=False,
            image_digest=None,
            runtime="echo",
        ),
        status="active",
    )
    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="stateless-v1", runtime_name="echo")

    async with session_env["factory"]() as db:
        idx = await db.get(SessionIndexRow, sid)
        assert idx is not None
        idx.last_event_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    reaped = await reap_idle(m, max_idle_seconds=60.0)
    assert sid in reaped
    assert session_env["registry"].get(sid) is None

    async with session_env["factory"]() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.status == "terminated"


@pytest.mark.asyncio
async def test_reap_idle_destroy_sandbox_when_pause_unsupported(session_env) -> None:
    class NoPauseSandbox(_DummySandbox):
        def __init__(self) -> None:
            super().__init__()
            self.destroyed = False

        async def pause(self) -> str:
            raise SnapshotUnsupported("no pause")

        async def destroy(self) -> None:
            self.destroyed = True

    class NoPauseProvider:
        name = "nopause"

        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            return NoPauseSandbox()

        async def resume(self, snapshot_id: str) -> NoPauseSandbox:
            return NoPauseSandbox()

        async def get(self, sandbox_id: str) -> None:
            return None

    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry

    provider = NoPauseProvider()
    factory = session_env["factory"]
    writer = JournalWriter(factory, blob_dir=session_env["writer"]._blob_dir)  # noqa: SLF001
    reader = JournalReader(factory, blob_dir=session_env["reader"]._blob_dir)  # noqa: SLF001
    registry = SessionRegistry()
    m = SessionManager(
        session_factory=factory,
        writer=writer,
        reader=reader,
        registry=registry,
        catalog=make_catalog(),
        sandbox_provider=provider,
        live_queue_max=8,
    )
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")

    async with factory() as db:
        idx = await db.get(SessionIndexRow, sid)
        assert idx is not None
        idx.last_event_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    reaped = await reap_idle(m, max_idle_seconds=60.0)
    assert sid in reaped
    assert m._sandboxes.get(sid) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_drain_marks_sticky_paused(session_env) -> None:
    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    await drain_for_deploy(m, timeout_seconds=2.0)
    assert session_env["registry"].get(sid) is None

    async with session_env["factory"]() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.status == "paused"


@pytest.mark.asyncio
async def test_resume_reuses_paused_sandbox(session_env) -> None:
    provider = PausingProvider()
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry

    factory = session_env["factory"]
    writer = JournalWriter(factory, blob_dir=session_env["writer"]._blob_dir)  # noqa: SLF001
    reader = JournalReader(factory, blob_dir=session_env["reader"]._blob_dir)  # noqa: SLF001
    registry = SessionRegistry()
    m = SessionManager(
        session_factory=factory,
        writer=writer,
        reader=reader,
        registry=registry,
        catalog=make_catalog(),
        sandbox_provider=provider,
        live_queue_max=8,
    )
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    sbx_before = m._sandboxes[sid]  # noqa: SLF001

    async with factory() as db:
        idx = await db.get(SessionIndexRow, sid)
        assert idx is not None
        idx.last_event_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    await reap_idle(m, max_idle_seconds=60.0)
    assert len(provider.sandboxes) == 1

    result = await m.resume(sid)
    assert result.state.value == "cold"
    assert m._sandboxes[sid] is sbx_before  # noqa: SLF001
    assert len(provider.sandboxes) == 1


@pytest.mark.asyncio
async def test_make_engine_postgres_uses_pool() -> None:
    from unittest.mock import patch

    from supervisor.db.engine import make_engine

    with patch("supervisor.db.engine.create_async_engine") as mock_create:
        make_engine("postgresql+asyncpg://user:pass@localhost/db")
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 10
        assert kwargs["pool_pre_ping"] is True
