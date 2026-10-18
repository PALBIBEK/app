"""WebSearch -- search the web via a nested provider web_search call."""

from __future__ import annotations

from typing import Any

from ..base import Tool, ToolContext, ToolResult, fail, ok
from .backend import format_or_passthrough, run_search
from .prompt import WEB_SEARCH_TOOL_NAME, get_web_search_prompt


def websearch_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if len(query) < 2:
            return fail("Error: Missing query" if not query else
                        "Error: query must be at least 2 characters")

        allowed = args.get("allowed_domains")
        blocked = args.get("blocked_domains")
        if allowed is not None and not isinstance(allowed, list):
            return fail("`allowed_domains` must be an array of strings.")
        if blocked is not None and not isinstance(blocked, list):
            return fail("`blocked_domains` must be an array of strings.")
        if allowed and blocked:
            return fail(
                "Error: Cannot specify both allowed_domains and "
                "blocked_domains in the same request"
            )

        try:
            output = run_search(
                query,
                allowed_domains=list(allowed) if allowed else None,
                blocked_domains=list(blocked) if blocked else None,
                search_fn=ctx.extra.get("web_search"),
                llm=ctx.extra.get("llm"),
                http_client=ctx.extra.get("http_client"),
            )
        except Exception as exc:  # noqa: BLE001
            return fail(f"WebSearch failed: {exc}")

        text = format_or_passthrough(output)
        return ok(
            text,
            query=output.get("query", query),
            durationSeconds=output.get("durationSeconds"),
            results=output.get("results"),
        )

    return Tool(
        name=WEB_SEARCH_TOOL_NAME,
        description=get_web_search_prompt(),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to use",
                    "minLength": 2,
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only include search results from these domains",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Never include search results from these domains",
                },
            },
            "required": ["query"],
        },
        run=run,
    )
