"""Model-facing `subagent_fork` — one-shot child with parent history seed.

DeepSeek: subagent-fork-in-process. The child sees completed parent turns
only; the open tool-call turn (this call) is excluded. Supports background.
"""

from __future__ import annotations

from typing import Any

from ..subagent import SubagentError, delegation_depth_of, map_run_to_outcome
from .base import Tool, ToolContext, ToolResult, fail, ok
from .subagent import _as_bool, fail_coded

DESCRIPTION = (
    "Delegate a follow-up task to a subagent that already sees this "
    "conversation's completed turns (not the current open tool call). "
    "Use when the child needs prior context; put only the new instructions "
    "in `prompt`. For a fully isolated task with no parent history, use "
    "`subagent` instead. By default waits for the child and returns only "
    "its final answer. Set `run_in_background` true to start asynchronously "
    "and receive a settlement notice later (or poll with `job_output`)."
)


def subagent_fork_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        description = str(args.get("description") or "").strip()
        if not prompt:
            return fail_coded(
                "subagent_fork requires a non-empty `prompt`",
                "BAD_ARGS",
            )

        parent_depth = delegation_depth_of(ctx.extra)
        if _as_bool(args.get("run_in_background")):
            jobs = ctx.extra.get("subagent_jobs")
            if jobs is None:
                return fail_coded(
                    "no subagent job registry is available on this agent",
                    "NO_PROVIDER",
                )
            try:
                job_id = jobs.start_fork(
                    prompt,
                    description=description,
                    parent_depth=parent_depth,
                )
            except SubagentError as exc:
                return fail_coded(str(exc), exc.code)
            except Exception as exc:  # noqa: BLE001 - tool must not kill the run
                return fail_coded(
                    f"subagent_fork failed to start in background: {exc}",
                    "SPAWN_ERROR",
                )
            return ok(
                f"started background subagent job {job_id}",
                job_id=job_id,
                description=description,
                mode="fork",
                background=True,
            )

        fork = ctx.extra.get("fork_subagent")
        if fork is None:
            return fail_coded(
                "no subagent fork spawner is available on this agent",
                "NO_PROVIDER",
            )

        try:
            outcome = fork(
                prompt,
                description=description,
                parent_depth=parent_depth,
            )
        except SubagentError as exc:
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001 - tool must not kill the run
            return fail_coded(
                f"subagent_fork failed to start or run: {exc}",
                "SPAWN_ERROR",
            )

        ok_flag, text = map_run_to_outcome(outcome)
        if ok_flag:
            return ok(
                text,
                subagent_id=outcome.id,
                description=outcome.description,
                stop_reason=outcome.stop_reason.value,
                mode="fork",
            )
        return fail_coded(
            text,
            "SUBAGENT_FAILED",
            subagent_id=outcome.id,
            stop_reason=outcome.stop_reason.value,
            mode="fork",
        )

    return Tool(
        name="subagent_fork",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Short label for the delegated work "
                        "(for logs / status, not the child prompt)."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Additional instructions for the child. Completed "
                        "parent turns are already in its context."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true, start the child asynchronously and return "
                        "a job id immediately. Default false (wait for result)."
                    ),
                },
            },
            "required": ["prompt"],
        },
        run=run,
    )
