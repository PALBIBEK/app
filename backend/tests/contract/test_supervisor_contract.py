"""The backend's AiClient against a live supervisor: every call it makes, with real bodies.

Unit tests fake the supervisor with the shapes someone believed it returns; these run
the real thing (CI's e2e stack, or ``./dev.sh``) and check the fields the backend reads.

    CONTRACT_SUPERVISOR_URL=http://127.0.0.1:8080 \\
    CONTRACT_SERVICE_JWT_SECRET=... pytest tests/contract

Skipped when ``CONTRACT_SUPERVISOR_URL`` is unset. The shipped githi-v1 composition on
the ``echo`` model: a user turn ``/tool <name> <json>`` makes echo call that tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from prime_agent.config import Settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.harness import HarnessBindRequest
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.harness_bind_service import HarnessBindService
from prime_agent.services.memory_service import agent_pin

URL = os.environ.get("CONTRACT_SUPERVISOR_URL", "")
pytestmark = [
    pytest.mark.skipif(not URL, reason="CONTRACT_SUPERVISOR_URL not set: no live supervisor"),
    pytest.mark.asyncio(loop_scope="module"),
]

TERMINAL = {"run_end"}


@pytest.fixture(scope="module")
def client() -> AiClient:
    return AiClient(Settings(
        environment="development",
        ai_supervisor_base_url=URL,
        ai_service_jwt_secret=os.environ.get("CONTRACT_SERVICE_JWT_SECRET", ""),
        ai_http_timeout_seconds=30,
    ))


@pytest.fixture(scope="module")
def user() -> TenantPrincipal:
    uid = f"contract-{uuid.uuid4().hex[:10]}"  # a fresh tenant: nothing left over from other runs
    return TenantPrincipal(tenant_id=uid, principal_id=uid)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pin(client: AiClient, user: TenantPrincipal):
    """What binding a harness to the default composition resolves to, from the live catalog."""
    return await HarnessBindService(client=client)._resolve(user, HarnessBindRequest())


async def _events(client: AiClient, user: TenantPrincipal, sid: str, *, runs: int = 1, timeout: float = 60) -> list[dict[str, Any]]:
    """The journal, read the way the backend relays it (raw SSE), until ``runs`` runs ended."""
    stream: AsyncIterator[bytes] = await client.stream_session_events(user, sid)
    out: list[dict[str, Any]] = []
    buf = b""

    async def read() -> None:
        nonlocal buf
        ended = 0
        async for chunk in stream:
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [line[5:].strip() for line in frame.split(b"\n") if line.startswith(b"data:")]
                if not data:
                    continue
                event = json.loads(b"".join(data))
                out.append(event)
                if event.get("type") in TERMINAL:
                    ended += 1
                    if ended >= runs:
                        return

    await asyncio.wait_for(read(), timeout)
    return out


async def test_the_default_composition_resolves_from_the_catalog(client, user, pin) -> None:
    offered = [c for c in await client.list_compositions(user) if c.artifact_id == "githi-v1"]
    assert offered, "githi-v1 is not offered"
    assert pin.composition_digest in {c.digest for c in offered}
    detail = await client.get_composition(user, "githi-v1", pin.composition_digest)
    assert detail.harness_version == pin.harness_version == "githi-v1"
    assert pin.runtime == (await client.get_harness_version(user, "githi-v1"))["runtime"]


async def test_a_turn_runs_and_streams_to_its_end(client, user, pin) -> None:
    created = await client.create_session(
        user, harness_version=pin.harness_version, runtime=pin.runtime,
        composition_id=pin.composition_id, composition_digest=pin.composition_digest,
    )
    sid = created["session_id"]
    assert created.get("status")

    sent = await client.post_message(user, sid, text="hello contract", command_id=f"c-{uuid.uuid4().hex}")
    assert sent["turn_id"] and sent["status"]
    assert isinstance(sent.get("queued"), bool)

    events = await _events(client, user, sid)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end", kinds
    assert all({"seq", "event_id", "type"} <= set(e) for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    turn_end = next(e for e in events if e["type"] == "turn_end")
    assert turn_end["data"]["stop_reason"] == "stop"

    # The message's turn_id names its run: fetched by id once ended, listed, cancel is a no-op.
    run = await client.get_run(user, sid, sent["turn_id"])
    assert run["run_id"] == sent["turn_id"] and run["status"] == "completed", run
    assert run["output_text"] and run["started_at_ms"] <= run["ended_at_ms"]
    assert [r["run_id"] for r in await client.list_runs(user, sid)] == [sent["turn_id"]]
    assert await client.cancel_run(user, sid, sent["turn_id"]) == run

    detail = await client.get_session(user, sid)  # what the session list refreshes from
    for key in ("status", "message_count", "total_cost", "last_seq"):
        assert key in detail, key
    assert detail["is_busy"] is False  # the run ended (read from the journal under Temporal)
    assert detail["last_seq"] >= events[-1]["seq"]

    # Resume where the reader stopped: the backend forwards Last-Event-ID, which is the
    # SSE ``id:`` field a browser saw: the event's seq.
    stream = await client.stream_session_events(user, sid, last_event_id=str(events[-2]["seq"]))
    first = await asyncio.wait_for(stream.__anext__(), 10)
    assert events[-1]["event_id"].encode() in first
    await stream.aclose()

    forked = await client.fork_session(user, sid)
    assert forked["session_id"] and forked["session_id"] != sid
    await client.terminate_session(user, forked["session_id"])

    assert await client.list_session_approvals(user, sid) == []
    assert isinstance(await client.list_session_children(user, sid), list)
    await client.terminate_session(user, sid)
    with pytest.raises(AiUpstreamError) as refused:
        await client.post_message(user, sid, text="after the end", command_id=f"c-{uuid.uuid4().hex}")
    assert refused.value.status_code == 409


async def test_the_agent_uses_a_tool_and_a_direct_invoke_answers_in_the_same_shape(client, user, pin) -> None:
    sid = (await client.create_session(
        user, harness_version=pin.harness_version, runtime=pin.runtime,
        composition_id=pin.composition_id, composition_digest=pin.composition_digest,
    ))["session_id"]
    await client.post_message(
        user, sid, text='/tool write {"path": "notes.txt", "content": "from the contract"}',
        command_id=f"c-{uuid.uuid4().hex}",
    )
    events = await _events(client, user, sid)
    pending = await client.list_session_approvals(user, sid)
    if pending:
        assert (await client.get_session(user, sid))["is_busy"] is True  # a run waits on a person
    for row in pending:  # a sandbox that asks: answer like the approve button does
        assert {"approval_id", "prompt", "options", "status", "timeout_s"} <= set(row)
        resolved = await client.respond_approval(user, row["approval_id"], choice="allow")
        assert resolved["status"]
    if pending:
        events = await _events(client, user, sid)
    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends and ends[-1]["data"].get("ok", True) is not False, ends

    invoked = await client.invoke_tool(user, sid, name="read", arguments={"path": "notes.txt"})
    assert {"tool_call_id", "ok", "payload"} <= set(invoked), invoked
    assert invoked["ok"] is True
    assert "from the contract" in json.dumps(invoked["payload"])
    await client.terminate_session(user, sid)


async def test_workspace_files_round_trip(client, user) -> None:
    scope = f"scope-{uuid.uuid4().hex[:8]}"
    await client.ensure_workspace_scope(user, scope)
    put = await client.put_workspace_file(user, scope, "docs/a.txt", b"alpha", mime_type="text/plain")
    assert put
    files = await client.list_workspace_files(user, scope)
    assert "docs/a.txt" in {f.get("path") for f in files}, files
    assert await client.get_workspace_file_bytes(user, scope, "docs/a.txt") == b"alpha"
    await client.delete_workspace_file(user, scope, "docs/a.txt")
    assert "docs/a.txt" not in {f.get("path") for f in await client.list_workspace_files(user, scope)}


async def test_notebooks_round_trip_and_stay_private(client, user, pin) -> None:
    agent = agent_pin(pin.composition_id, pin.composition_digest)
    nb = await client.create_notebook(user, agent_pin=agent, name=f"nb-{uuid.uuid4().hex[:6]}")
    assert nb["agent_pin"] == agent and nb["facts"] == 0
    added = await client.notebook_request(user, "POST", nb["id"], "facts",
                                          json_body={"facts": ["Prefers tabs", "Lives in Pune"]}, expected=(201,))
    assert added == {"added": 2}
    facts = await client.notebook_request(user, "GET", nb["id"], "facts")
    assert {f["text"] for f in facts} == {"Prefers tabs", "Lives in Pune"}
    assert {"id", "sensitivity", "created_at"} <= set(facts[0])
    hits = await client.search_notebooks(user, agent_pin=agent, query="Pune")
    assert any("Pune" in h["fact"] and h["notebook_id"] == nb["id"] for h in hits)
    await client.notebook_request(user, "DELETE", nb["id"], "facts",
                                  json_body={"ids": [facts[0]["id"]]}, expected=(204,))
    revisions = await client.notebook_request(user, "GET", nb["id"], "revisions")
    assert {"insert", "delete"} <= {r["op"] for r in revisions}
    assert nb["id"] in {n["id"] for n in await client.list_notebooks(user, agent_pin=agent)}

    stranger = TenantPrincipal(tenant_id=f"other-{uuid.uuid4().hex[:8]}", principal_id="other")
    with pytest.raises(AiUpstreamError) as hidden:
        await client.notebook_request(stranger, "GET", nb["id"], "facts")
    assert hidden.value.status_code == 404
    assert await client.list_notebooks(stranger, agent_pin=agent) == []


async def test_a_built_manifest_publishes_and_its_policy_holds_in_a_session(client, user) -> None:
    """The builder's artifacts are accepted as built, and a session runs on them as configured."""
    from types import SimpleNamespace

    from prime_agent.schemas.harness_manifest import default_manifest
    from prime_agent.services.harness_publish_service import HarnessPublisher

    manifest = default_manifest()
    manifest.sandbox.provider = "local_process"  # the e2e stack and ./dev.sh have no Docker
    manifest.sandbox.file_effects = "danger_full_access"  # so bash may run on the host sandbox
    manifest.policy.max_steps = 8
    harness = SimpleNamespace(id=str(uuid.uuid4()))
    pins = await HarnessPublisher(client)._artifacts(harness, manifest, 1, user)

    summary = await client.get_composition(user, pins["composition_id"], pins["composition_digest"])
    assert summary.status == "offered" and summary.harness_version == pins["harness_version"]
    version = await client.get_harness_version(user, pins["harness_version"])
    assert version["manifest"]["loop"]["max_steps"] == 8
    assert version["manifest"]["approvals"]["require_approval"] == ["bash"]

    sid = (await client.create_session(
        user, harness_version=pins["harness_version"], runtime=pins["runtime"],
        composition_id=pins["composition_id"], composition_digest=pins["composition_digest"],
    ))["session_id"]
    await client.post_message(user, sid, text='/tool bash {"command": "echo built"}', command_id=f"c-{uuid.uuid4().hex}")
    pending: list[dict[str, Any]] = []
    for _ in range(60):  # bash needs approval: the policy published above
        pending = await client.list_session_approvals(user, sid)
        if pending:
            break
        await asyncio.sleep(0.5)
    assert pending, "bash ran without the approval the policy requires"
    assert (await client.get_session(user, sid))["is_busy"] is True
    await client.respond_approval(user, pending[0]["approval_id"], choice="allow")
    events = await _events(client, user, sid)
    (end,) = [e for e in events if e["type"] == "tool_end"]
    assert end["data"]["result"]["ok"] is True, end
    assert "built" in end["data"]["result"]["payload"]["stdout"]
    await client.terminate_session(user, sid)
