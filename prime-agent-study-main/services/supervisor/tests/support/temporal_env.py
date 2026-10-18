"""A real Temporal dev server for the whole test session.

Set TEMPORAL_CLI_PATH to a local ``temporal`` binary to skip the download
(needed where temporal.download is unreachable).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    cli = os.environ.get("TEMPORAL_CLI_PATH")
    env = await WorkflowEnvironment.start_local(dev_server_existing_path=cli)
    try:
        yield env
    finally:
        await env.shutdown()
