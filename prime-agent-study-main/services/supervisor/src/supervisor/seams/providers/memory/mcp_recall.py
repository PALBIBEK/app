"""McpSemanticRecallProvider — external MCP memory search backend."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import httpx

from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider


class McpSemanticRecallProvider:
    """Recall via MCP ``tools/call`` — degrades to no recall on errors."""

    def __init__(
        self,
        *,
        server_url: str,
        tool_name: str = "memory_search",
        timeout: float = 30.0,
    ) -> None:
        if not server_url.strip():
            raise ValueError("server_url required")
        self._url = server_url.rstrip("/")
        self._tool_name = tool_name
        self._client = httpx.Client(timeout=timeout)
        self._null = NullContextMemoryProvider()

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            body["params"] = params
        resp = self._client.post(self._url, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise RuntimeError("MCP error")
        return data.get("result")

    def _extract_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result.strip()
        if not isinstance(result, dict):
            return ""
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for row in content:
                if isinstance(row, dict) and row.get("type") == "text":
                    parts.append(str(row.get("text", "")))
            return "\n".join(parts).strip()
        if isinstance(result.get("text"), str):
            return str(result["text"]).strip()
        return ""

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._null.estimate_tokens(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        return self._null.before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        _ = ctx
        query = (user_prompt or "").strip()
        if not query:
            return None
        try:
            result = self._rpc(
                "tools/call",
                {
                    "name": self._tool_name,
                    "arguments": {"query": query},
                },
            )
            text = self._extract_text(result)
        except Exception:  # noqa: BLE001 — degrade never fail run
            return None
        if not text:
            return None
        return RecallBlock(text=text, source="semantic")

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        return self._null.append_plug_state(ctx, custom_type, data)


assert isinstance(
    McpSemanticRecallProvider(server_url="http://127.0.0.1:9999"),
    ContextMemoryProvider,
)
