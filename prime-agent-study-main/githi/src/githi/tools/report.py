"""Child-only `report` tool — relay a self-contained update to the parent.

Installed only on continuable children (not one-shot spawn/fork, not the root).
Reporting does not end the child's turn or settle its Activation.
"""

from __future__ import annotations

from typing import Any

from ..subagent import SubagentError
from .base import Tool, ToolContext, ToolResult, fail, ok

DESCRIPTION = (
    "Deliver a self-contained result to the agent that started you. Call "
    "this once before you finish with actionable content; report earlier as "
    "well whenever a partial finding changes what that agent should do next. "
    "The parent shares your workspace but does not automatically receive your "
    "transcript, tool output, or reasoning — a closing remark such as \"done\" "
    "leaves it nothing it can use. Reporting never ends your turn."
)


def fail_coded(message: str, code: str, **details: Any) -> ToolResult:
    return fail(f"Error [{code}]: {message}", code=code, **details)


def report_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        output = str(args.get("output") or "").strip()
        if not output:
            return fail_coded(
                "report requires a non-empty `output`",
                "BAD_ARGS",
            )
        sink = ctx.extra.get("report_sink")
        if sink is None:
            return fail_coded(
                "report is only available on continuable subagents",
                "NO_PROVIDER",
            )
        try:
            message_id = sink(output)
        except SubagentError as exc:
            return fail_coded(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001 - tool must not kill the run
            return fail_coded(
                f"report failed to deliver: {exc}",
                "REPORT_ERROR",
            )
        return ok(
            f"report accepted by the agent that started you as message {message_id}",
            message_id=message_id,
        )

    return Tool(
        name="report",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "output": {
                    "type": "string",
                    "description": (
                        "Actionable content for your parent — a self-contained "
                        "result or progress update it can use without reading "
                        "your transcript."
                    ),
                },
            },
            "required": ["output"],
        },
        run=run,
    )
