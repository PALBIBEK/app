"""Encrypted history: with a codec key, Temporal stores only ciphertext (pi-temporal's codec).

A session runs a githi turn through a client and worker that hold the key. Read raw,
as the server keeps it, its history carries no message text and every payload is
sealed; read with the key, it replays.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from collections.abc import Callable

import pytest

from supervisor.agent.events import TurnEnd
from supervisor.config import TemporalSettings
from supervisor.temporal import rollout
from supervisor.temporal.client import connect
from supervisor.temporal.codec import ENCODING
from supervisor.temporal.contracts import CreateSession, Message, workflow_id
from supervisor.temporal.sessions import SessionWorkflows
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import FAST, WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

SECRET = "sk-live-do-not-store-0451"


def _encodings(node: object) -> list[bytes]:
    """The ``encoding`` of every payload in a history, as its JSON form holds them."""
    found: list[bytes] = []
    if isinstance(node, dict):
        meta = node.get("metadata")
        if "data" in node and isinstance(meta, dict) and "encoding" in meta:
            found.append(base64.b64decode(meta["encoding"]))
        for value in node.values():
            found += _encodings(value)
    elif isinstance(node, list):
        for value in node:
            found += _encodings(value)
    return found


async def test_the_server_keeps_only_ciphertext_and_the_key_replays_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    key = base64.b64encode(os.urandom(32)).decode()
    sealed = await connect(
        TemporalSettings(address=env.address, namespace=env.namespace, task_queue=env.queue, codec_key=key)
    )
    sessions = SessionWorkflows(sealed, task_queue=env.queue)
    state = await env.state()
    name, digest = await env.composition(state, "githi-v1")
    sid = uuid.uuid4().hex
    async with session_worker(sealed, state, task_queue=env.queue):
        await sessions.create(
            sid,
            CreateSession("t1", "githi-v1", composition_id=name, composition_digest=digest, principal_id="alice"),
            timing=FAST,
        )
        await sessions.submit(sid, Message("c1", f"write a note; the token is {SECRET}"))
        events = await env.wait_for(state, sid, run_ended())
        assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
        assert (await sessions.status(sid)).turns_completed == 1  # the key reads it back

    raw = await env.client.get_workflow_handle(workflow_id(sid)).fetch_history()  # no key
    text = raw.to_json()
    assert SECRET not in text, "the message reached the server in the clear"
    stored = _encodings(json.loads(text))
    assert stored, "the history holds payloads"
    assert set(stored) == {ENCODING}, set(stored)

    report = await rollout.replay(_one(raw), sealed.data_converter)
    assert report.ok and report.checked == 1, report.failures


async def _one(history: object):  # type: ignore[no-untyped-def]
    yield history
