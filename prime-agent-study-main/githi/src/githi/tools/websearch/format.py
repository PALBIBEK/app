"""Parse and format WebSearch results (Claude Code websearch.ts helpers)."""

from __future__ import annotations

import json
from typing import Any


def make_output_from_blocks(
    blocks: list[dict[str, Any]],
    *,
    query: str,
    duration_seconds: float,
) -> dict[str, Any]:
    """Mirror makeOutputFromSearchResponse from the TypeScript tool."""
    results: list[Any] = []
    text_acc = ""
    in_text = True

    for block in blocks:
        btype = block.get("type")
        if btype == "server_tool_use":
            if in_text:
                in_text = False
                if text_acc.strip():
                    results.append(text_acc.strip())
                text_acc = ""
            continue

        if btype == "web_search_tool_result":
            content = block.get("content")
            if not isinstance(content, list):
                error_code = "unknown"
                if isinstance(content, dict):
                    error_code = content.get("error_code", error_code)
                results.append(f"Web search error: {error_code}")
                continue
            hits = [
                {"title": r.get("title", ""), "url": r.get("url", "")}
                for r in content
            ]
            results.append({
                "tool_use_id": block.get("tool_use_id", ""),
                "content": hits,
            })

        if btype == "text":
            text = block.get("text", "")
            if in_text:
                text_acc += text
            else:
                in_text = True
                text_acc = text

    if text_acc:
        results.append(text_acc.strip())

    return {
        "query": query,
        "results": results,
        "durationSeconds": duration_seconds,
    }


def format_search_output(output: dict[str, Any]) -> str:
    """mapToolResultToToolResultBlockParam formatting from the TS tool."""
    query = output.get("query", "")
    results = output.get("results") or []
    parts = [f'Web search results for query: "{query}"\n']

    for result in results:
        if result is None:
            continue
        if isinstance(result, str):
            parts.append(result + "\n")
        else:
            content = result.get("content") or []
            if content:
                parts.append(f"Links: {json.dumps(content)}\n")
            else:
                parts.append("No links found.\n")

    parts.append(
        "\nREMINDER: You MUST include the sources above in your response "
        "to the user using markdown hyperlinks."
    )
    return "\n".join(parts).strip()
