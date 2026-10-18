"""D6 §5 D / T8–T14 — MCP optional compose; catalog wins; deny still applies."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.store import CompositionCatalog
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.composite_tools import CompositeToolProvider
from supervisor.seams.providers.mcp_tools import McpToolProvider
from supervisor.seams.types import ToolInvokeContext
from tests.unit.test_catalog_pins import _sample_tool_set


def test_d_without_mcp_url_behavior_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """§5 D: without MCP_SERVER_URL / pin, catalog-only provider."""
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set(tool_names=("echo",)))
    seams = build_runtime_seams(tool_provider=CatalogToolProvider(ts), mcp_server_url=None)
    names = [t.name for t in seams.tool_provider.list_tools()]
    assert names == ["echo"]
    assert not isinstance(seams.tool_provider, CompositeToolProvider)


def test_t8_mcp_mock_list_merge_catalog_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """T8: MCP tools merge into list_tools; catalog name wins collisions."""
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if payload["method"] == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "mcp echo should lose",
                                "inputSchema": {"type": "object"},
                            },
                            {
                                "name": "mcp_search",
                                "description": "extra",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"q": {"type": "string"}},
                                },
                            },
                        ]
                    },
                },
            )
        if payload["method"] == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [{"type": "text", "text": "from-mcp"}],
                        "isError": False,
                    },
                },
            )
        return httpx.Response(500, json={"error": "unexpected"})

    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set(tool_names=("echo",)))
    catalog = CatalogToolProvider(ts)
    mcp = McpToolProvider(server_url="http://mcp.test/rpc")
    mcp._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        composite = CompositeToolProvider(catalog, mcp)
        names = {t.name for t in composite.list_tools()}
        assert "echo" in names and "mcp_search" in names
        # Catalog wins: echo still returns catalog payload, not MCP.
        echo = composite.invoke(ToolInvokeContext(), "echo", {"text": "from-catalog"})
        assert echo.ok is True
        assert echo.payload == {"text": "from-catalog"}
        search = composite.invoke(ToolInvokeContext(), "mcp_search", {"q": "x"})
        assert search.ok is True
        assert "from-mcp" in str(search.payload)
    finally:
        mcp.close()


def test_d_mcp_down_typed_error_no_crash() -> None:
    """§5 D: MCP down → typed MCP_ERROR; process does not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    mcp = McpToolProvider(server_url="http://mcp.down/rpc")
    mcp._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = mcp.invoke(ToolInvokeContext(), "anything", {})
        assert result.ok is False
        assert result.code == "MCP_ERROR"
    finally:
        mcp.close()


@pytest.mark.asyncio
async def test_t14_mcp_write_named_tool_denied_under_read_only(session_env) -> None:
    """T14: MCP write-named tool still FILE_EFFECTS_DENIED under read_only."""
    from supervisor.agent.factory import create_runtime
    from supervisor.catalog.types import AgentComposition, DigestRef, SandboxProfile
    from tests.unit.test_catalog_pins import _sample_prompt_pack

    cat = CompositionCatalog()
    prof = cat.offer(
        SandboxProfile(
            name="d6-mcp-ro",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            provider="none",
            file_effects="read_only",
            approval_default="ask",
        )
    )
    pack = cat.offer(_sample_prompt_pack(name="d6-mcp-pack"))
    tools = cat.offer(_sample_tool_set(name="d6-mcp-ts", tool_names=("echo",)))
    comp = cat.offer(
        AgentComposition(
            name="d6-mcp-ro-comp",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="kg-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
        )
    )
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    # Even if MCP registered a write tool, sandbox deny must win before provider.
    denied = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="write",
        arguments={"path": "x"},
        tool_call_id="tc-mcp-write",
    )
    assert denied.result.ok is False
    assert denied.result.code == "FILE_EFFECTS_DENIED"
    await m.terminate(sid)
