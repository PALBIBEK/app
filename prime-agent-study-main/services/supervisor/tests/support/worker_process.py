"""A session worker in its own OS process, so tests can kill -9 it mid-turn.

Usage: python -m tests.support.worker_process <db_url> <blob_dir> <temporal_address> \
           <task_queue> <tool_delay_s>
Prints ``READY <home_queue>`` once polling.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from supervisor.agent.runtimes.echo.runtime import EchoRuntime
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.temporal.client import connect
from supervisor.temporal.worker import session_worker
from tests.support.platform import boot_platform


async def main(db_url: str, blob_dir: str, address: str, queue: str, delay: float) -> None:
    EchoRuntime.tool_delay_s = delay
    client = await connect(TemporalSettings(address=address, task_queue=queue))
    async with open_db(db_url) as (_, factory):
        state, _ = await boot_platform(factory, Path(blob_dir))
        async with session_worker(client, state, task_queue=queue) as activities:
            print(f"READY {activities.home_queue}", flush=True)
            await asyncio.Event().wait()


if __name__ == "__main__":
    db_url, blob_dir, address, queue, delay = sys.argv[1:6]
    asyncio.run(main(db_url, blob_dir, address, queue, float(delay)))
