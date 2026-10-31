"""Background subagent jobs from before subagents were sessions, ended where they stand.

Those jobs ran a transport-based subagent (``lab_echo``, ``nested_session``, ``acp``) in a
``SubagentJob`` workflow. Subagents are sessions now (``supervisor.session.children``), so
nothing starts one; a job still running when this deployed is answered here: its work is
retired, and its end is journalled once (keyed per job), as before, so the session that
started it hears that it ended. Both activities stay registered for those workflows.
"""

from __future__ import annotations

from typing import Final

from supervisor.agent.events import JobEnd, SubagentEnd
from supervisor.hands.keys import hand_key
from supervisor.hands.leases import Borrower
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.session import SessionManager
from supervisor.temporal.contracts import JobEndRequest, JobOutcome, JobSpec

RETIRED: Final = (
    "this background subagent job ran on a subagent transport that is retired; "
    "start the work again (subagents are now sessions of their own)"
)


class JobSteps:
    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader

    async def run(self, spec: JobSpec) -> JobOutcome:
        """End the job: its transport is retired (an end already journalled stands)."""
        if (ended := await self._ended(spec)) is not None:
            return ended
        return await self.end(JobEndRequest(spec, "error", RETIRED))

    async def end(self, req: JobEndRequest) -> JobOutcome:
        """Journal the job's end; returns the end that was actually recorded."""
        spec = req.spec
        if (lease := Borrower.from_dict(spec.request.get("lease"))) is not None:
            # However the job ends, the hand goes back to its session (a no-op if it was).
            await self._manager._leases.give_back(
                hand_key(spec.session_id, lease.hand), lease.holder, lease.token
            )
        if (ended := await self._ended(spec)) is not None:
            return ended
        if await self._manager.session_status(spec.session_id) is None:
            # The session was erased while the job stopped: there is no journal to end it in
            # (and retrying until it lands would retry for ever).
            return JobOutcome(spec.job_id, req.stop_reason, req.output)
        # The first end recorded (done, error, interrupted) wins: both are keyed per job.
        for event, key in (
            (JobEnd(job_id=spec.job_id, stop_reason=req.stop_reason, output=req.output), "end"),
            (SubagentEnd(run_id=spec.run_id, stop_reason=req.stop_reason, output=req.output), "subagent_end"),
        ):
            await self._manager._writer.append(
                spec.session_id, event, turn_id=None, trace_id=None, event_key=f"job:{spec.job_id}:{key}"
            )
        ended = await self._ended(spec)
        assert ended is not None
        return ended

    async def _ended(self, spec: JobSpec) -> JobOutcome | None:
        key = keyed_event_id(spec.session_id, f"job:{spec.job_id}:end")
        envelope = await self._reader.event(spec.session_id, key)
        if envelope is None or not isinstance(end := envelope.payload, JobEnd):
            return None
        return JobOutcome(end.job_id, end.stop_reason, end.output)
