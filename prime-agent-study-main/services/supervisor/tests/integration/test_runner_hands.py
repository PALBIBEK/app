"""BH8: a hand on the tenant's own machine, through a runner that dials out.

A real runner process (``python -m supervisor.runner``) polls the API over HTTP;
nothing connects to it. A session pins a named hand on that runner: its tool
calls run in the runner's directory, the files land on the runner's disk and
not in the default hand, and when the runner dies the next call is a workspace
reset the model can read, like any lost sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from supervisor.agent.events import HandLost, ToolEnd
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import SandboxProviderName
from supervisor.hands import runners
from supervisor.main import create_app
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.integration.test_many_hands import LOCAL, _offer
from tests.support.githi_env import FAST, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

SUPERVISOR_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def serve_api(state: SupervisorState, port: int) -> AsyncIterator[str]:
    server = uvicorn.Server(uvicorn.Config(create_app(state=state), host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@asynccontextmanager
async def runner_process(url: str, token: str, root: Path) -> AsyncIterator[asyncio.subprocess.Process]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "supervisor.runner", "--url", url, "--tenant", "t1", "--name", "office",
        "--token", token, "--root", str(root), cwd=str(SUPERVISOR_ROOT),
    )
    try:
        yield proc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _session_on_runner(env: Env, state: SupervisorState) -> str:
    import uuid

    from supervisor.catalog.core.types import DigestRef
    from supervisor.temporal.contracts import CreateSession

    catalog = state.composition_catalog
    assert catalog is not None
    default = await _offer(env, catalog, "sandbox_profile", "githi-docker-v1", **LOCAL)
    office = await _offer(
        env, catalog, "sandbox_profile", "githi-docker-v1",
        provider=SandboxProviderName.RUNNER, runner="office",
        image_digest=f"sha256:{hashlib.sha256(b'runner').hexdigest()}",
    )
    comp = await _offer(
        env, catalog, "composition", "githi-v1", sandbox_profile=default,
        hands=(("office", DigestRef(id=office.id, digest=office.digest)),),
    )
    sid = uuid.uuid4().hex
    await env.sessions.create(
        sid,
        CreateSession("t1", "githi-v1", composition_id=comp.id, composition_digest=comp.digest, principal_id="alice"),
        timing=FAST,
    )
    return sid


async def test_a_hand_on_a_tenants_runner(
    env: Env, model: Callable[..., ScriptedGateway], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runners, "LIVE_WITHIN_S", 2.0)  # notice a dead runner quickly
    gateway = model(
        ("write", {"path": "report.txt", "content": "made on the office machine", "hand": "office"}),
        ("read", {"path": "report.txt", "hand": "office"}),
    )
    state = await env.state()
    root = tmp_path / "runner-root"
    async with (
        session_worker(env.client, state, task_queue=env.queue),
        serve_api(state, _free_port()) as url,
        httpx.AsyncClient(base_url=url, headers={"X-Tenant-Id": "t1"}) as http,
    ):
        token = (await http.post("/v1/runners/office")).json()["token"]
        assert (await http.post("/v1/runners/office", headers={"X-Tenant-Id": "t2"})).status_code == 200
        async with runner_process(url, token, root):
            async with asyncio.timeout(30):  # the runner is online once it polls
                while not (await http.get("/v1/runners")).json()[0]["online"]:
                    await asyncio.sleep(0.2)
            sid = await _session_on_runner(env, state)
            await env.sessions.submit(sid, Message("c1", "write a report on the office machine"))
            first = await env.wait_for(state, sid, run_ended(1), timeout=60)

        # The runner is gone: its hand is lost, and the model is told so.
        await asyncio.sleep(2.5)
        gateway.calls = [("read", {"path": "report.txt", "hand": "office"})]
        await env.sessions.submit(sid, Message("c2", "read it again"))
        second = await env.wait_for(state, sid, run_ended(2), timeout=60)
        status = await state.manager.session_status(sid)
        # Why it was lost is on record: no shell into anything that held the files.
        (gone,) = [h for h in payloads(second, HandLost) if h.hand == "office"]
        assert gone.logs_ref is not None, gone
        saved = await http.get(f"/v1/sessions/{sid}/hand-logs/{gone.logs_ref}")
        elsewhere = await http.get(f"/v1/sessions/{sid}/hand-logs/{gone.logs_ref}", headers={"X-Tenant-Id": "t2"})

    write, read = payloads(first, ToolEnd)[:2]
    assert not write.is_error and not read.is_error, (write, read)
    assert read.result["payload"] == "made on the office machine"
    on_disk = list(root.glob("hand-*/report.txt"))
    assert len(on_disk) == 1 and on_disk[0].read_text() == "made on the office machine"
    default = state.manager._hands.sandbox(sid)
    assert default is None or not await default.exists("report.txt")  # never touched the default hand

    lost = payloads(second[len(first):], ToolEnd)[-1]
    assert lost.is_error and lost.result["code"] == "WORKSPACE_RESET", lost
    assert saved.status_code == 200 and elsewhere.status_code == 404, (saved, elsewhere)
    log = saved.text
    assert "runner 'office'" in log and "last polled" in log, log  # the failing part, named
    assert "write /workspace/report.txt (" in log and "-> runner offline" in log, log  # what it did, and where it broke
    assert status == "active"  # the session goes on without that hand


async def test_a_runner_token_opens_only_its_own_queue(env: Env, tmp_path: Path) -> None:
    state = await env.state()
    async with serve_api(state, _free_port()) as url, httpx.AsyncClient(base_url=url) as http:
        token = (await http.post("/v1/runners/office", headers={"X-Tenant-Id": "t1"})).json()["token"]
        own = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "t1"}
        assert (await http.get("/v1/runners/office/next?wait=0", headers=own)).status_code == 204
        for headers, path in (
            ({**own, "X-Tenant-Id": "t2"}, "/v1/runners/office/next?wait=0"),  # another tenant's runner
            (own, "/v1/runners/lab/next?wait=0"),  # another runner of the same tenant
            ({"Authorization": "Bearer yr_wrong", "X-Tenant-Id": "t1"}, "/v1/runners/office/next?wait=0"),
        ):
            assert (await http.get(path, headers=headers)).status_code == 401
        # Re-keying stops the old token.
        await http.post("/v1/runners/office", headers={"X-Tenant-Id": "t1"})
        assert (await http.get("/v1/runners/office/next?wait=0", headers=own)).status_code == 401
