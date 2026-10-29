"""SubagentJob: one background subagent job, durable on its own.

Started by its session's workflow (parent close policy ABANDON, so the session's
continue-as-new never cuts it off) and cancelled by it on interrupt or when the
session ends. The work runs on any worker with heartbeats; a lost worker means a
retry elsewhere, never a lost job. However it ends (done, error, interrupted),
exactly one JobEnd reaches the session's journal.
"""

from __future__ import annotations

import asyncio
import contextlib

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from supervisor.temporal.contracts import (
        JOB_FINISHED_SIGNAL,
        Activity,
        JobEndRequest,
        JobOutcome,
        JobSpec,
        workflow_id,
    )
    from supervisor.temporal.policies import policy

# Time limits, heartbeats and retries: supervisor.temporal.policies (RUN_JOB, END_JOB).


@workflow.defn(name="SubagentJob")
class SubagentJobWorkflow:
    @workflow.run
    async def run(self, spec: JobSpec) -> JobOutcome:
        try:
            outcome = await workflow.execute_activity(
                Activity.RUN_JOB,
                spec,
                result_type=JobOutcome,
                start_to_close_timeout=policy(Activity.RUN_JOB).start_to_close(spec.timing),
                heartbeat_timeout=policy(Activity.RUN_JOB).heartbeat_timeout(spec.timing),
                retry_policy=policy(Activity.RUN_JOB).retry_policy,
            )
        except (ActivityError, asyncio.CancelledError) as err:
            if is_cancelled_exception(err):  # interrupted: the session cancelled this job
                outcome = await self._end(spec, "interrupted")
            else:
                assert isinstance(err, ActivityError)
                cause = err.cause
                detail = cause.message if isinstance(cause, ApplicationError) else str(cause or err)
                outcome = await self._end(spec, "error", detail)
        # The session drops it from its running jobs; a closed session has none to drop.
        with contextlib.suppress(Exception):
            await workflow.get_external_workflow_handle(workflow_id(spec.session_id)).signal(
                JOB_FINISHED_SIGNAL, spec.job_id
            )
        return outcome

    @staticmethod
    async def _end(spec: JobSpec, stop_reason: str, output: str | None = None) -> JobOutcome:
        return await workflow.execute_activity(
            Activity.END_JOB,
            JobEndRequest(spec, stop_reason, output),
            result_type=JobOutcome,
            start_to_close_timeout=policy(Activity.END_JOB).start_to_close(spec.timing),
            heartbeat_timeout=policy(Activity.END_JOB).heartbeat_timeout(spec.timing),
            retry_policy=policy(Activity.END_JOB).retry_policy,
        )
