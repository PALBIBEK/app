"""MCP ToolProvider — JSON-RPC tools/list + tools/call over HTTP."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult


class McpToolProvider:
    """Provider: OpenAI-compat tool surface backed by an MCP HTTP endpoint.

    Couples only via ``server_url`` — MCP server is developed/deployed separately.
    """

    def __init__(
        self,
        *,
        server_url: str,
        timeout: float = 60.0,
    ) -> None:
        if not server_url.strip():
            raise ValueError("server_url required")
        self._url = server_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._closed = False

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
            raise RuntimeError(f"MCP HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"MCP error {err.get('code')}: {err.get('message', err)}"
            )
        return data.get("result")

    def list_tools(self) -> list[ToolDescriptor]:
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        out: list[ToolDescriptor] = []
        for row in tools:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", ""))
            if not name:
                continue
            schema = row.get("inputSchema") or row.get("parameters") or {}
            if not isinstance(schema, dict):
                schema = {}
            out.append(
                ToolDescriptor(
                    name=name,
                    description=str(row.get("description") or ""),
                    parameters=schema,
                )
            )
        return out

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        _ = ctx
        try:
            result = self._rpc(
                "tools/call",
                {"name": name, "arguments": args},
            )
        except Exception as exc:  # noqa: BLE001 — return typed tool error
            return ToolResult(
                ok=False,
                payload=None,
                error=str(exc),
                code="MCP_ERROR",
            )
        if not isinstance(result, dict):
            return ToolResult(ok=True, payload=result)
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            payload: Any = {"content": content, "text": "\n".join(texts)}
        else:
            payload = result
        is_error = bool(result.get("isError"))
        return ToolResult(
            ok=not is_error,
            payload=payload,
            error=str(result.get("error")) if is_error else None,
            code="MCP_TOOL_ERROR" if is_error else None,
        )

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True
