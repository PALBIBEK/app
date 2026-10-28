"""Pi Durable's PartialOutput: the reply shown while the model writes it.

The model step writes the reply so far a few times a second, outside the journal;
the event stream sends it as ``partial_output`` (no id, so never replayed); the row
goes once the reply is journalled.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from itertools import pairwise

import pytest

from supervisor.agent.events import MessageEnd
from supervisor.api.events import iter_journal_sse
from supervisor.journal.partials import PartialOutputs
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


async def test_the_reply_streams_while_it_is_written_and_is_gone_once_journalled(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(stream_chunks=["The ", "answer ", "is ", "42."], stream_pause=0.4)
    state = await env.state()
    partials = PartialOutputs(state.reader.session_factory)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "what is it?"))

        frames: list[str] = []

        async def follow() -> None:
            async for frame in iter_journal_sse(
                session_id=sid, cursor=-1, reader=state.reader, partials=partials, poll_secs=0.05
            ):
                frames.append(frame)
                if '"type":"run_end"' in frame.replace(" ", ""):
                    return

        await asyncio.wait_for(follow(), timeout=30)
        events = await env.wait_for(state, sid, run_ended())

    shown = [json.loads(f.removeprefix("data: ")) for f in frames if '"partial_output"' in f]
    texts = [s["data"]["text"] for s in shown]
    assert len(texts) >= 2 and texts[-1].startswith("The answer"), texts  # it grew as it streamed
    assert all(a == b[: len(a)] for a, b in pairwise(texts))  # only ever appended
    assert all(not f.startswith("id:") for f in frames if '"partial_output"' in f)  # never replayed
    assert payloads(events, MessageEnd)[-1].content[0]["text"] == "The answer is 42."
    assert await partials.get(sid) is None, "the partial is removed once the reply is journalled"
