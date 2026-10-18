"""Chaos durability probe — SIGKILL mid-turn / mid-tool / mid-approval, then restart."""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} never opened")


def _run_child(db_path: str, blob_dir: str, port: int, err_path: str) -> None:
    try:
        import uvicorn
        from supervisor.app import build_supervisor
        from supervisor.db.engine import open_db
        from supervisor.main import create_app
        from tests.support.session_env import make_catalog

        async def _serve() -> None:
            async with open_db(f"sqlite+aiosqlite:///{db_path}") as (_, factory):
                state = build_supervisor(
                    session_factory=factory,
                    catalog=make_catalog(),
                    blob_dir=blob_dir,
                )
                app = create_app(state=state)
                config = uvicorn.Config(
                    app, host="127.0.0.1", port=port, log_level="warning"
                )
                server = uvicorn.Server(config)
                await server.serve()

        asyncio.run(_serve())
    except Exception as exc:  # noqa: BLE001
        Path(err_path).write_text(repr(exc))
        raise


async def _http(base: str, method: str, path: str, **kwargs):  # type: ignore[no-untyped-def]
    import httpx

    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("X-Tenant-Id", "chaos")
    async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
        return await getattr(client, method)(path, headers=headers, **kwargs)


async def phase_prepare_and_kill(db_path: Path, blob_dir: Path, port: int) -> dict:
    err_path = db_path.parent / "child.err"
    if err_path.exists():
        err_path.unlink()

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_run_child,
        args=(str(db_path), str(blob_dir), port, str(err_path)),
        daemon=True,
    )
    proc.start()
    try:
        _wait_port(port)
    except RuntimeError:
        msg = err_path.read_text() if err_path.exists() else f"alive={proc.is_alive()}"
        raise RuntimeError(f"child failed to listen: {msg}") from None

    base = f"http://127.0.0.1:{port}"
    pid = proc.pid
    assert pid is not None

    r = await _http(
        base, "post", "/v1/sessions", json={"harness_version": "kg-v1", "runtime": "echo"}
    )
    assert r.status_code == 201, r.text
    sid_turn = r.json()["session_id"]

    r = await _http(
        base,
        "post",
        f"/v1/sessions/{sid_turn}/messages",
        json={"text": "hello chaos mid-turn", "command_id": "c-turn"},
    )
    assert r.status_code in (200, 202), r.text
    await asyncio.sleep(0.2)

    r = await _http(
        base, "post", "/v1/sessions", json={"harness_version": "kg-v1", "runtime": "echo"}
    )
    assert r.status_code == 201, r.text
    sid_tool = r.json()["session_id"]

    r = await _http(
        base, "post", "/v1/sessions", json={"harness_version": "kg-v1", "runtime": "echo"}
    )
    assert r.status_code == 201, r.text
    sid_appr = r.json()["session_id"]

    from supervisor.agent.events import ToolStart
    from supervisor.db.engine import open_db
    from supervisor.journal.writer import JournalWriter
    from supervisor.policy.approvals import ApprovalBroker

    async with open_db(f"sqlite+aiosqlite:///{db_path}") as (_, factory):
        writer = JournalWriter(factory, blob_dir=blob_dir)
        await writer.append(
            sid_tool,
            ToolStart(tool_call_id="mid-tool-1", tool_name="git_commit", args={}),
            turn_id="t-mid",
            trace_id=None,
        )
        aid = await ApprovalBroker(factory).request(
            sid_appr, "t-appr", "Allow destructive?", ["yes", "no"], 600
        )

    from sqlalchemy import select
    from supervisor.db.models import EventRow, SessionRow

    async with open_db(f"sqlite+aiosqlite:///{db_path}") as (_, factory):
        async with factory() as db:
            rows = (
                await db.execute(
                    select(EventRow.session_id, EventRow.seq).order_by(
                        EventRow.session_id, EventRow.seq
                    )
                )
            ).all()
            statuses = {
                r.id: r.status
                for r in (await db.execute(select(SessionRow))).scalars()
            }

    pre_seqs: dict[str, list[int]] = {}
    for sid, seq in rows:
        pre_seqs.setdefault(sid, []).append(int(seq))

    os.kill(pid, signal.SIGKILL)
    proc.join(timeout=5)
    assert not proc.is_alive()

    return {
        "sid_turn": sid_turn,
        "sid_tool": sid_tool,
        "sid_appr": sid_appr,
        "approval_id": aid,
        "pre_seqs": pre_seqs,
        "pre_statuses": statuses,
        "killed_pid": pid,
    }


async def phase_restart_and_check(db_path: Path, blob_dir: Path, planted: dict) -> dict:
    from supervisor.app import build_supervisor
    from supervisor.db.engine import open_db
    from supervisor.journal.reader import JournalReader
    from supervisor.policy.approvals import ApprovalBroker
    from supervisor.sandbox.local_docker import LABEL_SESSION, LocalDockerProvider
    from supervisor.sandbox.reaper import reap_orphans
    from supervisor.session.recovery import Recovery, ResumeState
    from tests.support.session_env import make_catalog
    from tests.unit.test_docker import FakeDockerClient

    report: dict = {"cases": {}}

    async with open_db(f"sqlite+aiosqlite:///{db_path}") as (_, factory):
        catalog = make_catalog()
        state = build_supervisor(
            session_factory=factory,
            catalog=catalog,
            blob_dir=blob_dir,
        )
        reader = JournalReader(factory, blob_dir=blob_dir)
        recovery = Recovery(
            registry=state.manager._registry,  # noqa: SLF001
            reader=reader,
            catalog=catalog,
        )

        sid = planted["sid_turn"]
        row = await state.manager._get_session_row(sid)  # noqa: SLF001
        decision = await recovery.decide(sid, session_row=row)
        envs = await reader.read(sid, since=-1)
        seqs = [e.seq for e in envs]
        gaps = [a + 1 for a, b in zip(seqs, seqs[1:]) if b != a + 1]
        types = [type(e.payload).__name__ for e in envs]
        report["cases"]["mid_turn"] = {
            "resume_state": decision.state.value,
            "seq_count": len(seqs),
            "seq_gaps": gaps,
            "contiguous": gaps == [],
            "last_types": types[-8:],
            "has_run_end": "RunEnd" in types,
            "session_status": row.status,
        }

        sid = planted["sid_tool"]
        row = await state.manager._get_session_row(sid)  # noqa: SLF001
        decision = await recovery.decide(sid, session_row=row)
        unpaired = await reader.unpaired_tool_starts(sid)
        report["cases"]["mid_tool"] = {
            "resume_state": decision.state.value,
            "unpaired_ids": [u.tool_call_id for u in unpaired],
            "ok": decision.state == ResumeState.NEEDS_REVIEW
            and decision.unpaired_tool_call_ids == ("mid-tool-1",),
        }

        sid = planted["sid_appr"]
        pending = await ApprovalBroker(factory).pending(sid)
        report["cases"]["mid_approval"] = {
            "pending_count": len(pending),
            "approval_survived": any(
                p.approval_id == planted["approval_id"] for p in pending
            ),
            "status": pending[0].status if pending else None,
        }

        pre = planted["pre_seqs"].get(planted["sid_turn"], [])
        post = [e.seq for e in await reader.read(planted["sid_turn"], since=-1)]
        report["seq_preservation"] = {
            "pre_max": max(pre) if pre else None,
            "post_max": max(post) if post else None,
            "no_loss": set(pre).issubset(set(post)),
            "pre_len": len(pre),
            "post_len": len(post),
        }

        client = FakeDockerClient()
        provider = LocalDockerProvider(client=client)
        live = await provider.create(
            image="sha256:harness",
            labels={LABEL_SESSION: planted["sid_turn"]},
        )
        dead = await provider.create(
            image="sha256:harness",
            labels={LABEL_SESSION: "unknown-session"},
        )
        term = await provider.create(
            image="sha256:harness",
            labels={LABEL_SESSION: planted["sid_tool"]},
        )
        from supervisor.db.models import SessionRow

        async with factory() as db:
            srow = await db.get(SessionRow, planted["sid_tool"])
            if srow:
                srow.status = "terminated"
                await db.commit()

        reattached: list[str] = []

        async def reattach(sid: str, sbx) -> None:  # type: ignore[no-untyped-def]
            reattached.append(sid)
            state.manager.reattach_sandbox(sid, sbx)

        stats = await reap_orphans(
            provider,
            lookup_session=state.manager.session_status,
            reattach=reattach,
        )
        report["orphans"] = {
            "stats": stats,
            "reattached": reattached,
            "active_kept": await provider.get(live.info.id) is not None,
            "unknown_destroyed": await provider.get(dead.info.id) is None,
            "terminated_destroyed": await provider.get(term.info.id) is None,
        }

        result = await state.manager.resume(planted["sid_turn"])
        report["cases"]["mid_turn"]["after_resume"] = result.state.value

        # SSE reconnect: replay from cursor should not invent gaps
        snap = await state.manager.snapshot_events(planted["sid_turn"])
        report["sse_reconnect"] = {
            "snapshot_len": len(snap),
            "seqs_contiguous": all(
                snap[i][0] + 1 == snap[i + 1][0] for i in range(len(snap) - 1)
            ),
        }

    return report


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "chaos.db"
        blob_dir = tmp_path / "blobs"
        blob_dir.mkdir()
        port = 18766

        print("=== CHAOS: prepare + SIGKILL ===")
        planted = await phase_prepare_and_kill(db_path, blob_dir, port)
        print(f"killed pid={planted['killed_pid']}")
        print(
            f"sessions: turn={planted['sid_turn'][:8]} "
            f"tool={planted['sid_tool'][:8]} appr={planted['sid_appr'][:8]}"
        )

        print("\n=== CHAOS: restart + inspect ===")
        report = await phase_restart_and_check(db_path, blob_dir, planted)
        print(json.dumps(report, indent=2))

        ok_tool = report["cases"]["mid_tool"]["ok"]
        ok_appr = report["cases"]["mid_approval"]["approval_survived"]
        ok_seq = (
            report["seq_preservation"]["no_loss"]
            and report["cases"]["mid_turn"]["contiguous"]
        )
        ok_orphan = (
            report["orphans"]["active_kept"]
            and report["orphans"]["unknown_destroyed"]
            and report["orphans"]["terminated_destroyed"]
        )
        mid_turn = report["cases"]["mid_turn"]
        print("\n=== VERDICT ===")
        print(f"mid_tool classification: {'PASS' if ok_tool else 'FAIL'}")
        print(f"mid_approval survival:   {'PASS' if ok_appr else 'FAIL'}")
        print(f"event seq integrity:     {'PASS' if ok_seq else 'FAIL'}")
        print(f"orphan reaping:          {'PASS' if ok_orphan else 'FAIL'}")
        print(
            f"mid_turn resume:         {mid_turn['resume_state']} -> "
            f"{mid_turn.get('after_resume')} (run_end={mid_turn['has_run_end']})"
        )
        print(
            f"sse reconnect contig:    "
            f"{'PASS' if report['sse_reconnect']['seqs_contiguous'] else 'FAIL'}"
        )
        return 0 if (ok_tool and ok_appr and ok_seq and ok_orphan) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
