"""Attachments and artifacts in a running githi session (real Temporal, scripted model).

The model reads a pinned attachment from ``.attachments/`` and writes a report to
``.outputs/``; the run records its inputs, and its end collects the report as an artifact.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ToolEnd
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

READ_ATTACHED = ("read", {"file_path": ".attachments/sales.csv"})
WRITE_OUTPUT = ("write", {"file_path": ".outputs/report.md", "content": "# Q4 report\nsales are up\n"})


async def test_a_run_reads_its_attachment_and_its_output_becomes_an_artifact(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(READ_ATTACHED, WRITE_OUTPUT)
    store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    await store.commit_blob("ws-data", "data/sales.csv", b"q3,10\n")
    await store.commit_blob("ws-data", "data/sales.csv", b"q4,12\n")
    state = await env.state(workspace_store=store)
    files = state.manager.files
    assert files is not None
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await files.attach(sid, "ws-data", "data/sales.csv", version=1, attached_by="alice")
        await env.sessions.submit(sid, Message("c1", "summarize the attached sales"))
        events = await env.wait_for(state, sid, run_ended())

    # The model was told where the attachment is and where outputs go (after the pinned prompt).
    first = gateway.seen[0]
    note = next(m.content for m in first[1:] if m.role == "system")
    assert ".attachments/sales.csv (data/sales.csv, version 1)" in note and ".outputs/" in note

    reads = [e for e in payloads(events, ToolEnd) if e.tool_name == "read"]
    assert reads and not reads[0].is_error and "q3,10" in str(reads[0].result)  # the pinned version

    turn = next(e.turn_id for e in events if type(e.payload).__name__ == "RunStart")
    assert turn is not None
    inputs = await files.run_inputs(sid, turn)
    assert [(i.kind, i.path, i.version) for i in inputs] == [("attachment", "data/sales.csv", 1)]

    (artifact,) = await files.artifacts(sid)
    assert (artifact.name, artifact.turn_id, artifact.mime_type) == ("report.md", turn, "text/markdown")
    _, data = await files.artifact_bytes(sid, artifact.id)
    assert data == b"# Q4 report\nsales are up\n"
