"""Model-facing `subagent_continuable` — durable child, ack at inbox accept."""

from __future__ import annotations

from typing import Any

from ..subagent import SubagentError, delegation_depth_of
from .base import Tool, ToolContext, ToolResult, fail, ok

DESCRIPTION = (
    "Start a long-lived subagent with a fresh conversation. Returns as soon "
    "as the child's inbox accepts the first prompt (does not wait for the "
    "child to finish). Later, use `send_message` to queue another turn, or "
    "`interrupt_agent` to cancel the current turn. When the child goes idle, "
    "you receive a settlement notice; you may still `send_message` to wake it. "
    "For a one-shot wait-for-result task, use `subagent` instead."
)


def fail_coded(message: str, code: str, **details: Any) -> ToolResult:
    return fail(f"Error [{code}]: {message}", code=code, **details)


def subagent_continuable_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        description = str(args.get("description") or "").strip()
        if not prompt:
            return fail_coded(
                "subagent_continuable requires a non-empty `prompt`",
                "BAD_ARGS",
            )
        registry = ctx.extra.get("subagent_continuable")
        if registry is None:
            return fail_coded(
                "no continuable subagent registry is available on this agent",
                "NO_PROVIDER",
            )
        parent_depth = delegation_depth_of(ctx.extra)
        try:
            child_id, message_id = registry.start(
                prompt,
                description=description,
                parent_depth=parent_depth,
            )
        except SubagentError as exc:
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001 - tool must not kill the run
            return fail_coded(
                f"subagent_continuable failed to start: {exc}",
                "SPAWN_ERROR",
            )
        return ok(
            f"started subagent {child_id}",
            subagent_id=child_id,
            message_id=message_id,
            kind="continuable",
            description=description,
        )

    return Tool(
        name="subagent_continuable",
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
                        "Full standalone instructions for the child's first turn. "
                        "It cannot see the parent conversation."
                    ),
                },
            },
            "required": ["prompt"],
        },
        run=run,
    )
