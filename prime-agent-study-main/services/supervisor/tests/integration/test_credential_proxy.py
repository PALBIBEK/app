"""The credential proxy: a tool server's secret is used by the supervisor and seen by nothing else.

A tenant stores its MCP server's credential in the vault through the secrets
API. A githi turn then calls that server's tool: the supervisor sends the
credential, the call succeeds, and the secret is in no journal event, no
prompt the model saw, no file or environment variable in the sandbox, and no
secrets-API response, even though the server echoes it back. (The events API
serves the journal, so the journal check covers it.)
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.agent.events import ToolEnd
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.main import create_app
from supervisor.sandbox.providers.local_process import LocalProcessProvider
from supervisor.seams.providers.tools import mcp_tools
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from supervisor.vault.credentials import mcp_secret_name
from tests.support.githi_env import WRITE, Env, payloads, run_ended
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.scripted import ScriptedGateway

SERVER = "http://tools.test/mcp"
SECRET = "s3cr3t-mcp-token-0d6f2a"


def _mcp(request: httpx.Request) -> httpx.Response:
    """An MCP server that wants the tenant's bearer token, and carelessly echoes it."""
    body = json.loads(request.content)
    auth = request.headers.get("authorization", "")
    if body["method"] == "tools/list":
        tool = {"name": "lookup", "description": "Look something up.", "inputSchema": {"type": "object"}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [tool]}})
    if auth != f"Bearer {SECRET}":
        result: dict[str, Any] = {"isError": True, "error": "unauthorized", "content": []}
    else:
        text = f"found it (authorized with {auth})"
        result = {"content": [{"type": "text", "text": text}]}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


@pytest.fixture
def mcp_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MCP_SERVER_URL", SERVER)  # every session's tools include this server
    real = httpx.Client

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(_mcp)
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp_tools.httpx, "Client", client)
    yield


def _api(state: Any) -> TestClient:
    return TestClient(create_app(state=state), headers={"X-Tenant-Id": "t1"})


async def test_a_tool_servers_secret_reaches_only_the_server(
    env: Env, model: Callable[..., ScriptedGateway], mcp_server: None, tmp_path: Path
) -> None:
    gateway = model(("lookup", {"q": "x"}), WRITE)
    compositions = bootstrap_composition_catalog(default_catalog_dir())
    await hydrate_composition_catalog(compositions, env.factory)  # type: ignore[arg-type]
    from supervisor.app import build_supervisor

    state = build_supervisor(
        session_factory=env.factory,  # type: ignore[arg-type]
        catalog=all_variants(shipped_harness_catalog()),
        blob_dir=env.blob_dir,
        sandbox_provider=LocalProcessProvider(base_dir=tmp_path / "sandboxes"),
        composition_catalog=compositions,
    )
    api = _api(state)
    name = mcp_secret_name(SERVER)
    assert name == "mcp:http://tools.test"
    put = api.put(f"/v1/secrets/{name}", json={"value": SECRET})
    assert put.status_code == 200 and SECRET not in put.text
    listed = api.get("/v1/secrets")
    assert listed.json() == {"names": [name]} and SECRET not in listed.text

    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "look it up and write a note"))
        events = await env.wait_for(state, sid, run_ended())

        sandbox = state.manager._hands.sandbox(sid)
        assert sandbox is not None, "the write tool brought up a sandbox"
        shell = await sandbox.exec(["sh", "-c", "env; cat $(find . -type f) 2>/dev/null"])
        assert SECRET.encode() not in shell.stdout + shell.stderr
        assert b"notes.txt" in (await sandbox.exec(["ls"])).stdout

    lookup = next(e for e in payloads(events, ToolEnd) if e.tool_name == "lookup")
    assert not lookup.is_error, lookup  # the server got the secret
    assert "[redacted]" in json.dumps(lookup.result, default=str)
    journal = json.dumps([e.payload for e in events], default=repr)
    assert SECRET not in journal
    prompts = json.dumps([[m.content for m in chat] for chat in gateway.seen], default=repr)
    assert SECRET not in prompts and "[redacted]" in prompts

    # A deleted secret is gone (the server refuses calls without it: see _mcp).
    assert api.delete(f"/v1/secrets/{name}").status_code == 204
    assert api.delete(f"/v1/secrets/{name}").status_code == 404
