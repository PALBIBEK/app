"""D6 FULL-PROD §4 High — custom endpoint tools: HttpTool + SSRF at invoke (not echo)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from supervisor.catalog.digests import content_digest
from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.types import ToolDefinition, ToolSet
from supervisor.seams.types import ToolInvokeContext

CUSTOM_HTTP_NAME = "d6prod_http_weather"


def _http_tool_set(*, endpoint: str = "https://api.example.com/weather") -> ToolSet:
    params = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "text": {"type": "string"},  # echo-shaped bait — must still hit HTTP
        },
    }
    body = {
        "name": CUSTOM_HTTP_NAME,
        "description": "HTTP custom tool — must not echo",
        "parameters": params,
        "http_url": endpoint,
    }
    digest = content_digest(body)
    return ToolSet(
        name="d6prod-http-tools",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        tools={CUSTOM_HTTP_NAME: digest},
        definitions={
            CUSTOM_HTTP_NAME: ToolDefinition(
                name=CUSTOM_HTTP_NAME,
                description=body["description"],
                parameters=params,
                digest=digest,
                http_url=endpoint,
            )
        },
    )


def test_prod_http_tool_registration_surface_exists() -> None:
    """Custom tools with endpoint bind via HttpTool surface (http_url + SSRF helpers)."""
    from supervisor.catalog import http_tools

    assert callable(getattr(http_tools, "invoke_http_tool", None)), (
        "D6 FULL-PROD: missing invoke_http_tool — custom endpoint tools need "
        "supervisor-mediated HTTP (not echo fallback)"
    )
    assert callable(getattr(http_tools, "validate_public_http_url", None)), (
        "D6 FULL-PROD: missing validate_public_http_url for SSRF re-check at invoke"
    )


def test_prod_custom_endpoint_invoke_hits_http_not_echo() -> None:
    """Invoke of http_url-backed custom tool must HTTP-call; must not return echo text."""
    from supervisor.catalog.http_tools import invoke_http_tool

    ts = _http_tool_set()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "source": "http-tool"}
    mock_response.text = '{"ok":true,"source":"http-tool"}'

    with patch("httpx.Client.post", return_value=mock_response) as post:
        # Direct HttpTool path
        direct = invoke_http_tool(
            "https://api.example.com/weather",
            {"city": "Berlin", "text": "should-not-echo"},
        )
        # Catalog provider path (bind → http_url on ToolDefinition)
        result = CatalogToolProvider(ts).invoke(
            ToolInvokeContext(extra={"ssrf_recheck": True}),
            CUSTOM_HTTP_NAME,
            {"city": "Berlin", "text": "should-not-echo"},
        )

    assert post.call_count >= 1, "expected outbound HTTP POST for HttpTool"
    assert direct.get("source") == "http-tool"
    assert result.ok is True, result
    assert result.code != "TOOL_NOT_IMPLEMENTED", result
    payload = result.payload if isinstance(result.payload, dict) else {}
    assert payload.get("text") != "should-not-echo", (
        "D6 FULL-PROD: custom endpoint tool used echo fallback instead of HTTP"
    )
    assert payload.get("source") == "http-tool", payload


def test_prod_http_tool_ssrf_recheck_at_invoke() -> None:
    """SSRF allowlist must re-run at invoke (DNS rebinding) — not only at bind/save."""
    from supervisor.catalog.http_tools import validate_public_http_url

    with pytest.raises(ValueError, match="(?i)private|reserved|public|http"):
        validate_public_http_url("http://127.0.0.1/secret")

    ts = _http_tool_set(endpoint="http://127.0.0.1/secret")
    result = CatalogToolProvider(ts).invoke(
        ToolInvokeContext(),
        CUSTOM_HTTP_NAME,
        {"city": "x"},
    )
    assert result.ok is False, result
    assert result.code in {"TOOL_INVOKE_ERROR", "SSRF_DENIED", "TOOL_HANDLER_MISSING"}
    err = (result.error or "").lower()
    assert "private" in err or "reserved" in err or "public" in err or "ssrf" in err, (
        f"expected SSRF denial message, got {result!r}"
    )
