"""Model-facing `job_kill` — cancel a running background subagent."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ok
from .subagent import fail_coded

DESCRIPTION = (
    "Cancel a running background subagent job by id. Sends an interrupt to "
    "that child only; the parent keeps working. A settlement notice still "
    "arrives when the child exits. No-op if the job already finished."
)


def job_kill_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return fail_coded(
                "job_kill requires a non-empty `job_id`",
                "BAD_ARGS",
            )
        jobs = ctx.extra.get("subagent_jobs")
        if jobs is None:
            return fail_coded(
                "no subagent job registry is available on this agent",
                "NO_PROVIDER",
            )
        ok_flag, text, details = jobs.kill(job_id)
        if not ok_flag:
            return fail_coded(text, details.get("code", "NOT_FOUND"), **{
                k: v for k, v in details.items() if k != "code"
            })
        return ok(text, **details)

    return Tool(
        name="job_kill",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Id returned when the background job started.",
                },
            },
            "required": ["job_id"],
        },
        run=run,
    )
