"""Model-facing `job_output` — recover a background subagent result."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ok
from .subagent import fail_coded

DESCRIPTION = (
    "Read the status and closing message of a background subagent job "
    "started with `subagent` / `subagent_fork` and `run_in_background` true. "
    "Use when you need the result and have not yet seen the settlement notice, "
    "or to check whether the job is still running."
)


def job_output_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return fail_coded(
                "job_output requires a non-empty `job_id`",
                "BAD_ARGS",
            )
        jobs = ctx.extra.get("subagent_jobs")
        if jobs is None:
            return fail_coded(
                "no subagent job registry is available on this agent",
                "NO_PROVIDER",
            )
        ok_flag, text, details = jobs.output_text(job_id)
        if not ok_flag:
            return fail_coded(text, details.get("code", "NOT_FOUND"), **{
                k: v for k, v in details.items() if k != "code"
            })
        return ok(text, **details)

    return Tool(
        name="job_output",
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
