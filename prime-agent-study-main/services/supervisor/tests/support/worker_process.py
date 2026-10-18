"""A session worker in its own OS process, so tests can kill -9 it mid-turn.

Usage: python -m tests.support.worker_process <db_url> <blob_dir> <temporal_address> \
           <task_queue> <spec_json>

spec: {"echo_delay_s": float, "scripted_calls": [[tool, args], ...], "slow_tool": [tool, s],
       "slow_subagent": s}
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
from tests.support.platform import boot_platform
from tests.support.scripted import ScriptedGateway, scripted_model, slow_subagent, slow_tool


async def main(db_url: str, blob_dir: str, address: str, queue: str, spec: dict) -> None:
    EchoRuntime.tool_delay_s = float(spec.get("echo_delay_s", 0))
    with ExitStack() as patches:
        if calls := spec.get("scripted_calls"):
            patches.enter_context(scripted_model(ScriptedGateway([tuple(c) for c in calls])))
        if slow := spec.get("slow_tool"):
            patches.enter_context(slow_tool(slow[0], float(slow[1])))
        if (seconds := spec.get("slow_subagent")) is not None:
            patches.enter_context(slow_subagent(float(seconds)))
        client = await connect(TemporalSettings(address=address, task_queue=queue))
        async with open_db(db_url) as (_, factory):
            state, _ = await boot_platform(factory, Path(blob_dir))
            async with session_worker(client, state, task_queue=queue) as activities:
                print(f"READY {activities.home_queue}", flush=True)
                await asyncio.Event().wait()


if __name__ == "__main__":
    db_url, blob_dir, address, queue, spec = sys.argv[1:6]
    asyncio.run(main(db_url, blob_dir, address, queue, json.loads(spec)))
