"""Background subagent jobs: the work and the end of one, run by its SubagentJob workflow.

Both run on any worker (the parent session need not be live there). The end is
keyed per job, so a retry, a late result after an interrupt, or a crash between
journalling and returning all leave exactly one JobEnd: the first one written.
"""

from __future__ import annotations

import asyncio

from supervisor.agent.events import JobEnd
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.seams import SubagentRequest
from supervisor.session import SessionManager
from supervisor.session.subagent_ops import journal_background_end
from supervisor.temporal.contracts import JobEndRequest, JobOutcome, JobSpec
from supervisor.temporal.steps import heartbeating


class JobSteps:
    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader

    async def run(self, spec: JobSpec) -> JobOutcome:
        """Run the job's provider call (heartbeating) and journal its end, once."""
        if (ended := await self._ended(spec)) is not None:
            return ended  # a retry after the end was journalled
        request = SubagentRequest(**spec.request)
        ops = await self._manager.subagent_job_ops(spec.session_id, request)
        result = await heartbeating(asyncio.to_thread(ops.run_background, spec.provider, request))
        return await self.end(JobEndRequest(spec, result.status or "ok", result.text or None))

    async def end(self, req: JobEndRequest) -> JobOutcome:
        """Journal the job's end; returns the end that was actually recorded."""
        spec = req.spec
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
