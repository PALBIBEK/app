"""Model-facing `send_message` — queue the next turn for a continuable child."""

from __future__ import annotations

from typing import Any

from ..subagent import SubagentError
from .base import Tool, ToolContext, ToolResult, ok
from .subagent import fail_coded

DESCRIPTION = (
    "Queue a message as the next turn for a continuable subagent started with "
    "`subagent_continuable`. Does not wait for the child to reply — returns "
    "when the message is accepted. Use after the start ack or after a "
    "settlement notice when you want the same child to continue."
)


def send_message_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        child_id = str(args.get("subagent_id") or args.get("agent_id") or "").strip()
        message = str(args.get("message") or "").strip()
        if not child_id:
            return fail_coded(
                "send_message requires a non-empty `subagent_id`",
                "BAD_ARGS",
            )
        if not message:
            return fail_coded(
                "send_message requires a non-empty `message`",
                "BAD_ARGS",
            )
        registry = ctx.extra.get("subagent_continuable")
        if registry is None:
            return fail_coded(
                "no continuable subagent registry is available on this agent",
                "NO_PROVIDER",
            )
        try:
            message_id = registry.send_message(child_id, message)
        except SubagentError as exc:
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001
            return fail_coded(
                f"send_message failed: {exc}",
                "SEND_ERROR",
            )
        return ok(
            f"message queued as the next turn for subagent {child_id}",
            subagent_id=child_id,
            message_id=message_id,
        )

    return Tool(
        name="send_message",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "subagent_id": {
                    "type": "string",
                    "description": "Id returned by `subagent_continuable`.",
                },
                "message": {
                    "type": "string",
                    "description": "Text for the child's next turn.",
                },
            },
            "required": ["subagent_id", "message"],
        },
        run=run,
    )
