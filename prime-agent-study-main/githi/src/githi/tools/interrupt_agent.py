"""Model-facing `interrupt_agent` — cancel a continuable child's current turn."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolResult, ok
from .subagent import fail_coded

DESCRIPTION = (
    "Request an interrupt for a continuable subagent's current turn. Queued "
    "follow-up messages are kept. Returns immediately; if the agent is already "
    "idle or unknown, this is a successful no-op."
)


def interrupt_agent_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        agent_id = str(
            args.get("agent_id") or args.get("subagent_id") or ""
        ).strip()
        if not agent_id:
            return fail_coded(
                "interrupt_agent requires a non-empty `agent_id`",
                "BAD_ARGS",
            )
        registry = ctx.extra.get("subagent_continuable")
        if registry is None:
            return fail_coded(
                "no continuable subagent registry is available on this agent",
                "NO_PROVIDER",
            )
        registry.interrupt(agent_id)
        return ok(
            f"interrupt requested for agent {agent_id}",
            agent_id=agent_id,
            accepted=True,
        )

    return Tool(
        name="interrupt_agent",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Continuable subagent id to interrupt.",
                },
            },
            "required": ["agent_id"],
        },
        run=run,
    )
