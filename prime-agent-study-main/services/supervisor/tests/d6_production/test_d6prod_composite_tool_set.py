"""D6 FULL-PROD §4 High — CompositeToolProvider exposes _tool_set / tool_class under MCP wrap."""

from __future__ import annotations

import json

import httpx

from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.composite_tools import CompositeToolProvider
from supervisor.seams.providers.mcp_tools import McpToolProvider


def test_prod_composite_preserves_tool_set_and_tool_class(shipped_catalog, monkeypatch) -> None:
    """When MCP wraps catalog, getattr(provider, '_tool_set') must still yield tool_class."""
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)

    githi = [
        a
        for a in shipped_catalog.list_artifacts("tool_set", status="offered")
        if a.name == "githi-tools"
    ]
    assert githi, "need offered githi-tools for tool_class fixture"
    ts = githi[0]
    # githi-tools definitions carry tool_class (e.g. read=pure).
    assert any(
        getattr(td, "tool_class", None) for td in ts.definitions.values()
    ), "githi-tools fixture missing tool_class metadata"

    catalog = CatalogToolProvider(ts)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if payload.get("method") == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "mcp_extra",
                                "description": "mcp only",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                },
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
        )

    mcp = McpToolProvider(server_url="http://mcp.test/rpc")
    mcp._client = httpx.Client(transport=httpx.MockTransport(handler))

    composite = CompositeToolProvider(catalog, mcp)

    # Production: sandbox policy uses getattr(…, "_tool_set") through MCP wrap.
    exposed = getattr(composite, "_tool_set", None)
    assert exposed is not None, (
        "D6 FULL-PROD: CompositeToolProvider must expose _tool_set when MCP wraps catalog "
        "(sandbox policy loses tool_class otherwise)"
    )
    assert exposed is ts or exposed.name == ts.name

    # tool_class must be reachable on definitions via the exposed ToolSet.
    read_td = exposed.definitions.get("read") or exposed.definitions.get("ls")
    assert read_td is not None
    assert getattr(read_td, "tool_class", None), (
        "D6 FULL-PROD: tool_class missing on Composite._tool_set definitions"
    )

    # Factory path with mcp_server_url must also preserve metadata.
    seams = build_runtime_seams(
        tool_provider=catalog,
        mcp_server_url="http://mcp.test/rpc",
    )
    # Inject mock client on whatever MCP leaf was composed.
    provider = seams.tool_provider
    assert isinstance(provider, CompositeToolProvider), (
        "expected CompositeToolProvider when mcp_server_url set"
    )
    # Replace MCP client's transport if present on second provider.
    for leaf in getattr(provider, "_providers", ()):
        if isinstance(leaf, McpToolProvider):
            leaf._client = httpx.Client(transport=httpx.MockTransport(handler))
    factory_ts = getattr(provider, "_tool_set", None)
    assert factory_ts is not None, (
        "D6 FULL-PROD: factory CompositeToolProvider lost _tool_set under MCP wrap"
    )
    assert any(
        getattr(td, "tool_class", None) for td in factory_ts.definitions.values()
    ), "factory composite must preserve tool_class via _tool_set"
