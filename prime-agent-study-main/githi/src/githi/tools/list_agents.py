"""Model-facing `list_agents` — inventory of continuable children.

DeepSeek: tool-subagent-list-agents (children scope only). One-shot Jobs are
never listed. Status: running | idle (live between turns — unused after park)
| ready (parked, resumable via send_message).
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolResult, ok
from .subagent import fail_coded

DESCRIPTION = (
    "List continuable subagents you started with `subagent_continuable`. "
    "Use this to recall ids for `send_message` or `interrupt_agent`. "
    "One-shot `subagent` / background jobs are not included. "
    "Status is `running` (turn in progress) or `ready` (parked after "
    "settlement; still addressable with send_message)."
)


def _render(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "(no subagents)"
    lines: list[str] = []
    for entry in entries:
        label = entry.get("label") or "(no label)"
        lines.append(f"{entry['id']} [{entry['status']}] — {label}")
    return "\n".join(lines)


def _project(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map registry snapshots to model-facing child entries."""
    out: list[dict[str, Any]] = []
    for item in raw:
        internal = item.get("status", "ready")
        live = bool(item.get("live"))
        if internal == "running":
            status = "running"
        elif live:
            status = "idle"
        else:
            status = "ready"
        out.append({
            "kind": "child",
            "id": item["id"],
            "label": item.get("description") or "",
            "status": status,
        })
    return out


def list_agents_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        registry = ctx.extra.get("subagent_continuable")
        if registry is None:
            return fail_coded(
                "no continuable subagent registry is available on this agent",
                "NO_PROVIDER",
            )
        # scope is accepted for DeepSeek parity but only `children` is supported.
        scope = str(args.get("scope") or "children").strip().lower()
        if scope and scope not in ("children", "descendants"):
            return fail_coded(
                "list_agents scope must be 'children' or 'descendants'",
                "BAD_ARGS",
            )
        if scope == "descendants":
            # Flat registry: same as children until nested continuables exist.
            pass
        entries = _project(registry.list_children())
        return ok(
            _render(entries),
            agents=entries,
            scope="children",
            count=len(entries),
        )

    return Tool(
        name="list_agents",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["children", "descendants"],
                    "description": (
                        "Which agents to list. Default children (direct "
                        "continuable subagents). Descendants is accepted but "
                        "currently returns the same flat list."
                    ),
                },
            },
            "required": [],
        },
        run=run,
    )
