"""A session worker in its own OS process, so tests can kill -9 it mid-turn.

Usage: python -m tests.support.worker_process <db_url> <blob_dir> <temporal_address> \
           <task_queue> <spec_json>

spec: {"echo_delay_s": float, "scripted_calls": [[tool, args], ...], "one_at_a_time": bool,
       "slow_tool": [tool, s], "slow_subagent": s, "workspace_dir": path (a shared checkpoint store),
       "slow_end_run": s (closing a run waits this long first)}
Prints ``READY <home_queue>`` once polling.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack
from pathlib import Path

from supervisor.agent.runtimes.echo.runtime import EchoRuntime
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.temporal.client import connect
from supervisor.temporal.worker import session_worker
from tests.support.dir_storage import shared_workspace_store
from tests.support.platform import boot_platform
from tests.support.scripted import (
    ScriptedGateway,
    scripted_model,
    slow_end_run,
    slow_subagent,
    slow_tool,
)


async def main(db_url: str, blob_dir: str, address: str, queue: str, spec: dict) -> None:
    EchoRuntime.tool_delay_s = float(spec.get("echo_delay_s", 0))
    with ExitStack() as patches:
        if (calls := spec.get("scripted_calls")) is not None:
            gateway = ScriptedGateway([tuple(c) for c in calls], one_at_a_time=bool(spec.get("one_at_a_time")))
            patches.enter_context(scripted_model(gateway))
        if slow := spec.get("slow_tool"):
            patches.enter_context(slow_tool(slow[0], float(slow[1])))
        if (seconds := spec.get("slow_end_run")) is not None:
            patches.enter_context(slow_end_run(float(seconds)))
        if (seconds := spec.get("slow_subagent")) is not None:
            patches.enter_context(slow_subagent(float(seconds)))
        client = await connect(TemporalSettings(address=address, task_queue=queue))
        async with open_db(db_url) as (_, factory):
            store = (
                shared_workspace_store(factory, Path(spec["workspace_dir"]))
                if spec.get("workspace_dir")
                else None
            )
            state, _ = await boot_platform(factory, Path(blob_dir), workspace_store=store)
            async with session_worker(client, state, task_queue=queue) as activities:
                print(f"READY {activities.home_queue}", flush=True)
                await asyncio.Event().wait()


if __name__ == "__main__":
    db_url, blob_dir, address, queue, spec = sys.argv[1:6]
    asyncio.run(main(db_url, blob_dir, address, queue, json.loads(spec)))
