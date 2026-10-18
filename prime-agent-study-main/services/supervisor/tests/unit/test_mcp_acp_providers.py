"""oop-mcp-acp — McpToolProvider + AcpSubagentProvider (R/G/R)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from supervisor.seams.providers.acp_subagent import AcpSubagentProvider
from supervisor.seams.providers.composite_tools import CompositeToolProvider
from supervisor.seams.providers.mcp_tools import McpToolProvider
from supervisor.seams.providers.null_tools import NullToolProvider
from supervisor.seams.types import SubagentRequest, ToolInvokeContext


def test_ma_u1_mcp_list_tools() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        assert payload["method"] == "tools/list"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "search",
                            "description": "web search",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                            },
                        }
                    ]
                },
            },
        )

    client = McpToolProvider(server_url="http://mcp.test/rpc")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
    finally:
        client.close()


def test_ma_u2_mcp_invoke_call() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "search"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "isError": False,
                },
            },
        )

    client = McpToolProvider(server_url="http://mcp.test/rpc")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        out = client.invoke(ToolInvokeContext(), "search", {"q": "yal"})
        assert out.ok is True
        assert "ok" in str(out.payload)
    finally:
        client.close()


def test_ma_u3_composite_prefers_first_provider() -> None:
    null = NullToolProvider()
    composite = CompositeToolProvider(null)
    assert composite.list_tools() == []
    assert composite.invoke(ToolInvokeContext(), "missing", {}).code == "TOOL_NOT_FOUND"


def test_ma_u4_acp_spawn() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/initialize"):
            return httpx.Response(
                200,
                json={"protocolVersion": "1", "capabilities": {}},
            )
        payload = json.loads(request.content.decode())
        assert payload["prompt"] == "research topic"
        return httpx.Response(
            200,
            json={"text": "done", "child_id": "acp-child-1", "status": "ok"},
        )

    provider = AcpSubagentProvider(base_url="http://acp.test")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = provider.start_one_shot(
            SubagentRequest(prompt="research topic", description="task")
        )
        assert result.text == "done"
        assert result.child_id == "acp-child-1"
        assert provider.started is True
    finally:
        provider.close()


def test_ma_f1_factory_registers_acp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_BASE_URL", "http://acp.test")
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    from supervisor.seams.factory import build_runtime_seams

    seams = build_runtime_seams(register_acp=True)
    names = seams.subagent_transport._registry.names()  # type: ignore[attr-defined]
    assert "acp" in names
    assert "memory" in names


def test_ma_f2_factory_mcp_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SERVER_URL", "http://mcp.test/rpc")
    monkeypatch.delenv("ACP_BASE_URL", raising=False)
    from supervisor.seams.factory import build_runtime_seams

    with patch.object(McpToolProvider, "list_tools", return_value=[]):
        seams = build_runtime_seams()
        assert isinstance(seams.tool_provider, McpToolProvider)


def test_ma_n01_acp_registered_spawn_works(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.seams.factory import build_runtime_seams
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    provider = AcpSubagentProvider(base_url="http://unused")
    with patch.object(
        provider,
        "start_one_shot",
        return_value=__import__(
            "supervisor.seams.types", fromlist=["SubagentRunResult"]
        ).SubagentRunResult(text="x", status="ok"),
    ):
        transport.register_provider("acp", provider)
        out = transport.start_one_shot("acp", SubagentRequest(prompt="hi"))
        assert out.text == "x"


def test_ma_c1_contract_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    assert (root / "contracts" / "mcp_acp.md").is_file()


def test_ma_a1_providers_exist() -> None:
    providers = Path(__file__).resolve().parents[2] / "src" / "supervisor" / "seams" / "providers"
    assert (providers / "mcp_tools.py").is_file()
    assert (providers / "acp_subagent.py").is_file()


def test_ma_a2_journal_session_api_no_mcp_imports() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = ("McpToolProvider", "AcpSubagentProvider", "CompositeToolProvider")
    for pkg in ("journal", "session", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"
