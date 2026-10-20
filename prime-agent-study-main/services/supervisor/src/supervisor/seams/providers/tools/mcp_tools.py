"""MCP ToolProvider — JSON-RPC tools/list + tools/call over HTTP."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult

_REDACTED = "[redacted]"


class McpToolProvider:
    """Provider: OpenAI-compat tool surface backed by an MCP HTTP endpoint.

    Couples only via ``server_url`` — MCP server is developed/deployed separately.

    The supervisor is the credential proxy: a server that wants a credential
    gets it from the tenant's vault (``authorize``), on the supervisor's own
    request. The model and the sandbox never hold it, and a server that echoes
    it back has it redacted from what the model sees.
    """

    def __init__(
        self,
        *,
        server_url: str,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not server_url.strip():
            raise ValueError("server_url required")
        self._url = server_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._closed = False
        self._secret: str | None = None

    @property
    def server_url(self) -> str:
        return self._url

    def authorize(self, secret: str | None) -> None:
        """Send ``Authorization: Bearer <secret>`` from now on (None: send none)."""
        self._secret = secret or None

    def _redact(self, text: str) -> str:
        return text.replace(self._secret, _REDACTED) if self._secret else text

    def _scrub(self, value: Any) -> Any:
        if not self._secret:
            return value
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        return value

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {self._secret}"} if self._secret else None
        resp = self._client.post(self._url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(self._redact(f"MCP HTTP {resp.status_code}: {resp.text}"))
        data = self._scrub(resp.json())
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
