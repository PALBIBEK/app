"""A real Temporal dev server for the whole test session.

Set TEMPORAL_CLI_PATH to a local ``temporal`` binary to skip the download
(needed where temporal.download is unreachable).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHistory
from temporalio.testing import WorkflowEnvironment

from tests.support.workflow_histories import sealed

SUGGEST_CONTINUE_AS_NEW_AFTER = 60


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    cli = os.environ.get("TEMPORAL_CLI_PATH")
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=cli,
        # Suggest continue-as-new after a short history so every test also exercises it.
        dev_server_extra_args=[
            "--dynamic-config-value",
            f"limit.historyCount.suggestContinueAsNew={SUGGEST_CONTINUE_AS_NEW_AFTER}",
        ],
    )
    try:
        yield env
        if out := os.environ.get("RECORD_HISTORIES"):
            print(f"\nrecorded {await record_histories(env.client, Path(out))} histories to {out}")
    finally:
        await env.shutdown()


async def record_histories(client: Client, out: Path, *, per_shape: int = 1) -> int:
    """Save one history per distinct shape for the release replay gate
    (tests/fixtures/histories). Run the suite with RECORD_HISTORIES=<dir>.

    A shape is the workflow type, the steps it scheduled, the signals it got and the
    patched paths it took (by patch id: every patch marker has the same name). Sessions
    still running count too: held messages and stops usually end a test there, and a
    running history replays as far as it got.
    """
    out.mkdir(parents=True, exist_ok=True)
    shapes: dict[tuple[str, ...], int] = {}
    saved = 0
    query = "WorkflowType = 'AgentSession' or WorkflowType = 'SubagentJob'"
    async for run in client.list_workflows(query):
        history = await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
        if sealed(history) or not any(
            e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED for e in history.events
        ):
            continue  # sealed: the release gate replays without the key
        steps = sorted({
            e.activity_task_scheduled_event_attributes.activity_type.name
            for e in history.events
            if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        })
        signals = sorted({
            e.workflow_execution_signaled_event_attributes.signal_name
            for e in history.events
            if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
        })
        shape = (run.workflow_type, *steps, "|", *signals, "|", *sorted(patch_ids(history)))
        if shapes.get(shape, 0) >= per_shape:
            continue
        shapes[shape] = shapes.get(shape, 0) + 1
        doc = {"workflow_id": history.workflow_id, "history": json.loads(history.to_json())}
        (out / f"{run.workflow_type}-{saved:03d}.json").write_text(json.dumps(doc, separators=(",", ":")))
        saved += 1
    return saved


def patch_ids(history: WorkflowHistory) -> set[str]:
    """The patched paths a history took, as ``workflow.patched`` named them (a signal's
    per-message ids, ``held-message-2``, read as ``held-message``)."""
    found: set[str] = set()
    for e in history.events:
        if e.event_type != EventType.EVENT_TYPE_MARKER_RECORDED:
            continue
        payloads = e.marker_recorded_event_attributes.details.get("patch-data")
        if payloads is None or not payloads.payloads:
            continue
        found.add(re.sub(r"-\d+$", "", json.loads(payloads.payloads[0].data)["id"]))
    return found
