"""SessionManager unit tests (echo + in-memory journal)."""

from __future__ import annotations

import ast
import asyncio
import hashlib
from pathlib import Path

import pytest

from supervisor.agent.events import RunEnd
from supervisor.db.models import SessionRow
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import make_manifest
from tests.support.waits import counted, journalled


@pytest.mark.asyncio
async def test_create_returns_id_and_writes_sessions_row(session_env) -> None:
    m: SessionManager = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    assert sid
    async with session_env["factory"]() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.tenant_id == "t1"
        assert row.harness_version == "kg-v1"
        assert row.runtime == "echo"
        assert row.status == "active"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_create_requires_sandbox_false_never_calls_provider(session_env) -> None:
    m: SessionManager = session_env["manager"]
    provider = session_env["provider"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    assert provider.create_calls == []
    await m.terminate(sid)


async def _githi_with_real_image(session_env) -> str:  # type: ignore[no-untyped-def]
    """A githi harness pinning a real-looking image (the fixture's githi-v1 pins a placeholder)."""
    digest = "sha256:" + hashlib.sha256(b"githi-image").hexdigest()
    manifest = make_manifest("githi-real", requires_sandbox=True, image_digest=digest, runtime="githi")
    await session_env["catalog"].register(manifest)
    return manifest.harness_version


@pytest.mark.asyncio
async def test_a_githi_session_builds_its_sandbox_only_when_a_tool_needs_it(session_env) -> None:
    m: SessionManager = session_env["manager"]
    provider = session_env["provider"]
    harness = await _githi_with_real_image(session_env)
    sid = await m.create(tenant_id="t1", harness_version=harness, runtime_name="githi")
    assert provider.create_calls == [], "the recipe is resolved, the sandbox is not built"
    assert m._hands.recipe(sid) is not None and m._hands.sandbox(sid) is None
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_a_config_that_could_never_get_a_sandbox_is_refused_at_create(
    session_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    m: SessionManager = session_env["manager"]
    provider = session_env["provider"]

    async def unreachable(*, image: str | None = None) -> None:
        raise ConnectionError("Error while fetching server API version")

    monkeypatch.setattr(provider, "preflight", unreachable)
    harness = await _githi_with_real_image(session_env)
    with pytest.raises(ConnectionError):
        await m.create(
            tenant_id="t1", harness_version=harness, runtime_name="githi", session_id="doomed"
        )
    assert "doomed" not in m._hands and provider.create_calls == [], "nothing is left behind"


@pytest.mark.asyncio
async def test_send_produces_events_in_journal_and_live_queue(session_env) -> None:
    m: SessionManager = session_env["manager"]
    reader = session_env["reader"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    q = m.live_queue(sid)

    await m.send(sid, "hello", command_id="c1")
    live = []
    for _ in range(50):
        try:
            env = await asyncio.wait_for(q.get(), timeout=1)
        except TimeoutError:
            break
        live.append(env)
        if isinstance(env.payload, RunEnd):
            break

    journaled = await reader.read(sid, since=-1)
    assert len(journaled) >= 1
    assert len(live) == len(journaled)
    assert [e.seq for e in live] == [e.seq for e in journaled]
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_journal_append_precedes_live_publish(session_env) -> None:
    m: SessionManager = session_env["manager"]
    order: list[str] = []
    real_append = m._writer.append

    async def spy_append(*args, **kwargs):
        seq = await real_append(*args, **kwargs)
        order.append(f"journal:{seq}")
        return seq

    m._writer.append = spy_append  # type: ignore[method-assign]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    q = m.live_queue(sid)
    orig_put = q.put_nowait

    def spy_put(item) -> None:
        order.append(f"live:{item.seq}")
        orig_put(item)

    q.put_nowait = spy_put  # type: ignore[method-assign]

    await m.send(sid, "hello", command_id="c1")
    for _ in range(50):
        try:
            env = await asyncio.wait_for(q.get(), timeout=1)
        except TimeoutError:
            break
        if isinstance(env.payload, RunEnd):
            break

    assert order
    for i, entry in enumerate(order):
        if entry.startswith("live:"):
            seq = entry.split(":")[1]
            j = f"journal:{seq}"
            assert j in order
            assert order.index(j) < i
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_full_live_queue_does_not_block_journal(session_env) -> None:
    factory = session_env["factory"]
    writer = session_env["writer"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    provider = session_env["provider"]
    m = SessionManager(
        session_factory=factory,
        writer=writer,
        reader=reader,
        registry=SessionRegistry(),
        catalog=catalog,
        sandbox_provider=provider,
        live_queue_max=1,
    )
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    q = m.live_queue(sid)
    q.put_nowait(object())  # type: ignore[arg-type]

    await m.send(sid, "hello", command_id="c1")
    # A full live queue must not stop the journal: the whole turn is still recorded.
    await journalled(reader, sid, counted("RunEnd"))
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_terminate_is_idempotent(session_env) -> None:
    m: SessionManager = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    await m.terminate(sid)
    await m.terminate(sid)
    await m.terminate("missing-session-id")


@pytest.mark.asyncio
async def test_runtime_failure_writes_terminal_event(session_env) -> None:
    m: SessionManager = session_env["manager"]
    reader = session_env["reader"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    runtime = session_env["registry"].get(sid)

    async def boom():
        raise RuntimeError("runtime exploded")
        yield  # pragma: no cover

    runtime.events = boom  # type: ignore[method-assign]
    m._start_fanout(sid, runtime)
    await journalled(reader, sid, counted("ErrorEvent"))
    await m.terminate(sid)


def test_manager_module_imports_nothing_from_runtimes() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "session"
        / "manager.py"
    )
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "runtimes" not in node.module, node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "runtimes" not in alias.name
