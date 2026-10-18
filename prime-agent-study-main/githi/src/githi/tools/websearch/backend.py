"""OpenRouter / Anthropic-backed web search execution.

Claude Code nests a model call with Anthropic's `web_search_20250305` server
tool. githi defaults to OpenRouter, so the live backend uses
`openrouter:web_search` the same way: one nested completion that forces a
search, then formats the returned annotations/content.

Tests inject `ctx.extra["web_search"]` and never hit the network.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from .format import format_search_output, make_output_from_blocks

SearchFn = Callable[..., dict[str, Any]]

MAX_USES = 8


def openrouter_web_search(
    query: str,
    *,
    api_key: str,
    model: str,
    base_url: str = "https://openrouter.ai/api/v1",
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Run a nested chat completion with OpenRouter's web_search server tool."""
    start = time.perf_counter()
    params: dict[str, Any] = {
        "max_results": 8,
        "max_uses": MAX_USES,
    }
    if allowed_domains:
        params["allowed_domains"] = list(allowed_domains)
    if blocked_domains:
        params["excluded_domains"] = list(blocked_domains)

    # OpenRouter chat/completions rejects Anthropic-style
    # tool_choice {"type":"tool","name":"web_search"} for server tools
    # ("Tool '' not found in provided tools"). Use OpenAI-style "required"
    # so the nested call always searches.
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an assistant for performing a web search tool use",
            },
            {
                "role": "user",
                "content": f"Perform a web search for the query: {query}",
            },
        ],
        "tools": [
            {"type": "openrouter:web_search", "parameters": params},
        ],
        "tool_choice": "required",
    }

    owns = client is None
    client = client or httpx.Client(timeout=120.0)
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    finally:
        if owns:
            client.close()

    duration = time.perf_counter() - start
    blocks = _blocks_from_openrouter(data)
    if blocks:
        return make_output_from_blocks(
            blocks, query=query, duration_seconds=duration)

    # Fallback: flatten message content + annotations into the same shape.
    message = (data.get("choices") or [{}])[0].get("message") or {}
    results: list[Any] = []
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        results.append(content.strip())
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif isinstance(part, str):
                text_parts.append(part)
        if text_parts:
            results.append("".join(text_parts).strip())

    hits = []
    for ann in message.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        url_citation = ann.get("url_citation") or ann
        title = url_citation.get("title") or url_citation.get("url") or ""
        url = url_citation.get("url") or ""
        if url:
            hits.append({"title": title, "url": url})
    if hits:
        results.append({"tool_use_id": "openrouter", "content": hits})

    if not results:
        results.append("No search results returned.")

    return {
        "query": query,
        "results": results,
        "durationSeconds": duration,
    }


def _blocks_from_openrouter(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort extraction if the provider returns Anthropic-style blocks."""
    message = (data.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list) and any(
        isinstance(p, dict) and p.get("type") in (
            "web_search_tool_result", "server_tool_use")
        for p in content
    ):
        return [p for p in content if isinstance(p, dict)]
    return []


def run_search(
    query: str,
    *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    search_fn: SearchFn | None = None,
    llm: Any = None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    if search_fn is not None:
        return search_fn(
            query,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )

    if llm is None:
        raise RuntimeError(
            "WebSearch needs an LLM client (or an injected web_search backend)."
        )

    api_key = getattr(llm, "api_key", "") or ""
    model = getattr(llm, "model", "") or ""
    base_url = getattr(llm, "base_url", "https://openrouter.ai/api/v1")
    if not api_key:
        raise RuntimeError("WebSearch requires an API key on the LLM client.")

    return openrouter_web_search(
        query,
        api_key=api_key,
        model=model,
        base_url=base_url,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        client=http_client,
    )


def format_or_passthrough(output: dict[str, Any]) -> str:
    return format_search_output(output)
