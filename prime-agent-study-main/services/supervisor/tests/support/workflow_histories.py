"""The workflow histories a test server recorded, for determinism replays."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHistory

from supervisor.temporal.codec import ENCODING


async def replayable_histories(client: Client, query: str) -> AsyncIterator[WorkflowHistory]:
    """Every history matching ``query`` that has at least one completed workflow task.

    A run no worker has picked up yet (only ``WorkflowExecutionStarted``) holds
    no workflow code to check, and the SDK's replayer waits for a task that
    never comes, so a replay test that met one hung until its timeout.
    """
    async for run in client.list_workflows(query):
        history = await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
        if sealed(history):
            continue  # encrypted (test_sealed_history replays its own with the key)
        if any(e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED for e in history.events):
            yield history


def sealed(history: WorkflowHistory) -> bool:
    """Whether any payload in the run was sealed by the codec (from its start, or once a key
    was set partway): no replayer without the key reads it."""
    return _SEALED in history.to_json()


_SEALED = base64.b64encode(ENCODING).decode()  # how a payload's encoding shows in JSON
