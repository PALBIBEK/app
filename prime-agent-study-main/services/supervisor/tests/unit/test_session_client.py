"""The session client retries an update the workflow was not ready for, and nothing else."""

from __future__ import annotations

import pytest
from temporalio.service import RPCError, RPCStatusCode

from supervisor.temporal import sessions

NOT_READY = RPCError(
    "Unable to perform workflow execution update due to unexpected workflow task failure.",
    RPCStatusCode.FAILED_PRECONDITION,
    b"\x08\t\x12T...\x1aJ\nHtype.googleapis.com/temporal.api.errordetails.v1.WorkflowNotReadyFailure",
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(sessions.asyncio, "sleep", instant)


async def test_a_not_ready_workflow_gets_the_update_again() -> None:
    attempts: list[int] = []

    async def send() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise NOT_READY
        return "accepted"

    assert await sessions._delivered(send) == "accepted" and len(attempts) == 3


async def test_other_failures_and_a_workflow_never_ready_are_raised() -> None:
    async def other() -> None:
        raise RPCError("nope", RPCStatusCode.FAILED_PRECONDITION, b"")

    with pytest.raises(RPCError, match="nope"):
        await sessions._delivered(other)

    async def never() -> None:
        raise NOT_READY

    with pytest.raises(RPCError, match="workflow task failure"):
        await sessions._delivered(never)
