"""Background subagent jobs: the work and the end of one, run by its SubagentJob workflow.

Both run on any worker (the parent session need not be live there). The end is
keyed per job, so a retry, a late result after an interrupt, or a crash between
journalling and returning all leave exactly one JobEnd: the first one written.
"""

from __future__ import annotations

import asyncio

from temporalio import activity

from supervisor.agent.events import JobEnd
from supervisor.hands.keys import hand_key
from supervisor.hands.leases import Borrower
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.seams import SubagentRequest
from supervisor.session import SessionManager
from supervisor.session.subagent_ops import journal_background_end
from supervisor.temporal.contracts import JobEndRequest, JobOutcome, JobSpec
from supervisor.temporal.hand_jobs import run_hand_job
from supervisor.temporal.steps import heartbeating


class JobSteps:
    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader

    async def run(self, spec: JobSpec) -> JobOutcome:
        """Run the job's provider call (heartbeating) and journal its end, once."""
        if (ended := await self._ended(spec)) is not None:
            return ended  # a retry after the end was journalled
        fields = dict(spec.request)
        lease = Borrower.from_dict(fields.pop("lease", None))
        request = SubagentRequest(**fields)
        if lease is not None:  # it works in a hand its session lent it
            # A retried attempt (its worker died, a deploy stopped it) resumes from the
            # journal; a first one has nothing to read back.
            retried = activity.in_activity() and activity.info().attempt > 1
            done = await run_hand_job(
                self._manager, spec, request, lease, reader=self._reader if retried else None
            )
            return await self.end(JobEndRequest(spec, done.stop_reason, done.text or None))
        ops = await self._manager.subagent_job_ops(spec.session_id, request)
        result = await heartbeating(asyncio.to_thread(ops.run_background, spec.provider, request))
        return await self.end(JobEndRequest(spec, result.status or "ok", result.text or None))

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
        await journal_background_end(
            self._manager._writer,
            spec.session_id,
            job_id=spec.job_id,
            run_id=spec.run_id,
            stop_reason=req.stop_reason,
            output=req.output,
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
