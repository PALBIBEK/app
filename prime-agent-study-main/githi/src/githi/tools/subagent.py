"""Model-facing `subagent` tool — one-shot spawn (foreground or background).

DeepSeek: packages/subagent/tool-subagent. Foreground waits for the child;
background returns a job id and settles later via next_turn / follow_up.
"""

from __future__ import annotations

from typing import Any

from ..subagent import SubagentError, delegation_depth_of, map_run_to_outcome
from .base import Tool, ToolContext, ToolResult, fail, ok

DESCRIPTION = (
    "Delegate a self-contained task to a fresh subagent with its own "
    "conversation. The child does not see this transcript — put every "
    "file path, constraint, and acceptance criterion in `prompt`. "
    "By default waits for the child and returns only its final answer. "
    "Set `run_in_background` true to start the child asynchronously, get "
    "a job id immediately, and receive a settlement notice later "
    "(or poll with `job_output`). Prefer this for isolated research or "
    "implementation work you can summarize; keep asking the user "
    "(ask_user_question) on the parent when a human decision is required."
)


def fail_coded(message: str, code: str, **details: Any) -> ToolResult:
    return fail(f"Error [{code}]: {message}", code=code, **details)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def subagent_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        description = str(args.get("description") or "").strip()
        if not prompt:
            return fail_coded(
                "subagent requires a non-empty `prompt`",
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
                job_id = jobs.start_spawn(
                    prompt,
                    description=description,
                    parent_depth=parent_depth,
                )
            except SubagentError as exc:
                return fail_coded(str(exc), exc.code)
            except Exception as exc:  # noqa: BLE001 - tool must not kill the run
                return fail_coded(
                    f"subagent failed to start in background: {exc}",
                    "SPAWN_ERROR",
                )
            return ok(
                f"started background subagent job {job_id}",
                job_id=job_id,
                description=description,
                mode="spawn",
                background=True,
            )

        spawn = ctx.extra.get("spawn_subagent")
        if spawn is None:
            return fail_coded(
                "no subagent spawner is available on this agent",
                "NO_PROVIDER",
            )

        try:
            outcome = spawn(
                prompt,
                description=description,
                parent_depth=parent_depth,
            )
        except SubagentError as exc:
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001 - tool must not kill the run
            return fail_coded(
                f"subagent failed to start or run: {exc}",
                "SPAWN_ERROR",
            )

        ok_flag, text = map_run_to_outcome(outcome)
        if ok_flag:
            return ok(
                text,
                subagent_id=outcome.id,
                description=outcome.description,
                stop_reason=outcome.stop_reason.value,
            )
        return fail_coded(
            text,
            "SUBAGENT_FAILED",
            subagent_id=outcome.id,
            stop_reason=outcome.stop_reason.value,
        )

    return Tool(
        name="subagent",
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
                        "Full standalone instructions for the child. "
                        "It cannot see the parent conversation."
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
