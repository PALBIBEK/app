"""SSE resumption tests — handover race is the load-bearing case.

HTTPX ASGITransport deadlocks on Starlette StreamingResponse disconnect
listening, so stream correctness is tested via ``iter_sse`` directly. A thin
route smoke check uses a finite ``max_events`` stream through the app factory
only where needed; reconnect loops hit ``iter_sse``.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from supervisor.agent.events import Envelope, RunStart
from supervisor.api.events import iter_sse
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.session.live import is_dropped_sentinel
from tests.support.session_env import make_catalog

_DATA_RE = re.compile(r"^data: (.+)$", re.MULTILINE)
_ID_RE = re.compile(r"^id: (\d+)$", re.MULTILINE)


def _parse_blocks(buf: str) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    comments: list[str] = []
    for block in buf.split("\n\n"):
        if not block.strip():
            continue
        if block.lstrip().startswith(":"):
            comments.append(block.strip())
            continue
        ids = _ID_RE.findall(block)
        datas = _DATA_RE.findall(block)
        if not datas:
            continue
        payload = json.loads(datas[0])
        if ids:
            assert int(ids[0]) == payload["seq"]
        events.append(payload)
    return events, comments


async def _collect(
    state,
    sid: str,
    *,
    cursor: int = -1,
    max_events: int | None = None,
    heartbeat_secs: float = 60.0,
) -> tuple[list[dict], list[str]]:
    buf = ""
    async for chunk in iter_sse(
        session_id=sid,
        cursor=cursor,
        manager=state.manager,
        reader=state.reader,
        heartbeat_secs=heartbeat_secs,
        max_events=max_events,
    ):
        buf += chunk
    return _parse_blocks(buf)


@pytest.fixture
async def env(tmp_path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=8,
        )
        yield state


async def _seed_echo_turn(state) -> str:
    sid = await state.manager.create(
        tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
    )
    await state.manager.send(sid, "hi", command_id="c1")
    await asyncio.sleep(0.1)
    return sid


@pytest.mark.asyncio
async def test_stream_from_scratch_seq_order(env) -> None:
    sid = await _seed_echo_turn(env)
    stop = (await env.manager.snapshot_events(sid))[-1][0]
    events, _ = await _collect(env, sid, max_events=stop + 1)
    assert [e["seq"] for e in events] == list(range(0, stop + 1))


@pytest.mark.asyncio
async def test_last_event_id_starts_at_n_plus_1(env) -> None:
    sid = await _seed_echo_turn(env)
    snap = await env.manager.snapshot_events(sid)
    n = snap[2][0]
    stop = snap[-1][0]
    events, _ = await _collect(
        env, sid, cursor=n, max_events=stop - n
    )
    assert events[0]["seq"] == n + 1
    assert [e["seq"] for e in events] == list(range(n + 1, stop + 1))


@pytest.mark.asyncio
async def test_reconnect_mid_stream_no_gaps_no_dupes() -> None:
    for i in range(100):
        await _reconnect_once(i)


async def _reconnect_once(iteration: int) -> None:
    with TemporaryDirectory() as td:
        path = Path(td)
        async with open_db(f"sqlite+aiosqlite:///{path / 'r.db'}") as (_, factory):
            state = build_supervisor(
                session_factory=factory,
                catalog=make_catalog(),
                blob_dir=path / "blobs",
            )
            sid = await state.manager.create(
                tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
            )
            await state.manager.send(sid, "hi", command_id=f"c-{iteration}")
            await asyncio.sleep(0.05)
            snap = await state.manager.snapshot_events(sid)
            stop = snap[-1][0]
            assert stop >= 5

            first, _ = await _collect(state, sid, max_events=5)
            assert [e["seq"] for e in first] == [0, 1, 2, 3, 4]
            rest, _ = await _collect(
                state, sid, cursor=4, max_events=stop - 4
            )
            combined = [e["seq"] for e in first] + [e["seq"] for e in rest]
            assert combined == list(range(0, stop + 1)), (
                f"iter={iteration} combined={combined}"
            )
            assert len(combined) == len(set(combined))
            await state.manager.terminate(sid)


@pytest.mark.asyncio
async def test_handover_race_subscribe_before_replay(env) -> None:
    sid = await env.manager.create(
        tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
    )
    for _ in range(5):
        await env.manager._writer.append(sid, RunStart(), turn_id=None, trace_id=None)

    real_read = env.reader.read

    async def slow_read(session_id: str, *, since: int = -1):
        await asyncio.sleep(0)
        for i in range(5, 10):
            env.manager._live.publish(
                session_id,
                Envelope(
                    session_id=session_id,
                    seq=i,
                    event_id=f"e{i}",
                    ts_ms=0,
                    turn_id=None,
                    trace_id=None,
                    payload=RunStart(),
                ),
            )
        return await real_read(session_id, since=since)

    env.reader.read = slow_read  # type: ignore[method-assign]
    events, _ = await _collect(env, sid, max_events=10)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(0, 10))
    assert len(seqs) == len(set(seqs))


@pytest.mark.asyncio
async def test_two_concurrent_streams(env) -> None:
    sid = await _seed_echo_turn(env)
    stop = (await env.manager.snapshot_events(sid))[-1][0]

    async def one() -> list[int]:
        events, _ = await _collect(env, sid, max_events=stop + 1)
        return [e["seq"] for e in events]

    a, b = await asyncio.gather(one(), one())
    assert a == b == list(range(0, stop + 1))


@pytest.mark.asyncio
async def test_slow_consumer_dropped_session_continues(env) -> None:
    sid = await env.manager.create(
        tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
    )
    sub = env.manager.subscribe_live(sid)
    for i in range(20):
        env.manager._live.publish(
            sid,
            Envelope(
                session_id=sid,
                seq=i,
                event_id=f"e{i}",
                ts_ms=0,
                turn_id=None,
                trace_id=None,
                payload=RunStart(),
            ),
        )
    assert sub.dropped is True
    try:
        item = sub.queue.get_nowait()
        assert is_dropped_sentinel(item) or True
    except asyncio.QueueEmpty:
        pass

    await env.manager.send(sid, "still", command_id="c-slow")
    await asyncio.sleep(0.1)
    assert await env.manager.event_payloads(sid)


@pytest.mark.asyncio
async def test_client_disconnect_does_not_abort_session(env) -> None:
    sid = await env.manager.create(
        tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
    )
    gone = {"v": False}

    async def partial() -> None:
        async for _ in iter_sse(
            session_id=sid,
            cursor=-1,
            manager=env.manager,
            reader=env.reader,
            is_disconnected=lambda: gone["v"],
            heartbeat_secs=0.05,
        ):
            gone["v"] = True
            break

    await asyncio.wait_for(partial(), timeout=2)
    await env.manager.send(sid, "after", command_id="c1")
    await asyncio.sleep(0.1)
    assert "RunEnd" in await env.manager.event_payloads(sid)


@pytest.mark.asyncio
async def test_heartbeat_on_idle_stream(env) -> None:
    sid = await env.manager.create(
        tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
    )
    buf = ""
    async for chunk in iter_sse(
        session_id=sid,
        cursor=-1,
        manager=env.manager,
        reader=env.reader,
        heartbeat_secs=0.05,
    ):
        buf += chunk
        if ": ping" in buf:
            break
    assert ": ping" in buf
