"""D6 FULL-PROD §5 C — arbitrary custom names must NOT silently echo-fallback."""

from __future__ import annotations

from supervisor.catalog.digests import content_digest
from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.tool_handlers import resolve_handler
from supervisor.catalog.types import ToolDefinition, ToolSet
from supervisor.seams.types import ToolInvokeContext


ARBITRARY = "d6prod_orphan_widget"


def _orphan_tool_set() -> ToolSet:
    """Custom name with echo-shaped schema — MVP silently maps to _echo; prod must not."""
    body = {
        "name": ARBITRARY,
        "description": "orphan custom — no endpoint, no MCP, no register_handler",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
    }
    digest = content_digest(body)
    return ToolSet(
        name="d6prod-orphan-tools",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        tools={ARBITRARY: digest},
        definitions={
            ARBITRARY: ToolDefinition(
                name=ARBITRARY,
                description=body["description"],
                parameters=body["parameters"],
                digest=digest,
            )
        },
    )


def test_prod_resolve_handler_no_silent_echo_for_arbitrary_name() -> None:
    """resolve_handler must not return echo for unregistered custom names."""
    params = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    handler = resolve_handler(ARBITRARY, parameters=params)
    assert handler is None, (
        "D6 FULL-PROD: resolve_handler silently returned echo-shaped fallback for "
        f"{ARBITRARY!r} — expect TOOL_HANDLER_MISSING at bind or TOOL_NOT_IMPLEMENTED "
        "at invoke (no silent echo)"
    )


def test_prod_catalog_invoke_orphan_is_not_implemented_or_missing() -> None:
    """Listed-but-unregistered custom → TOOL_NOT_IMPLEMENTED (not ok echo payload)."""
    provider = CatalogToolProvider(_orphan_tool_set())
    result = provider.invoke(
        ToolInvokeContext(),
        ARBITRARY,
        {"text": "must-not-succeed-as-echo"},
    )
    assert result.ok is False, (
        f"D6 FULL-PROD: orphan custom invoke succeeded via echo fallback: {result!r}"
    )
    assert result.code in {"TOOL_NOT_IMPLEMENTED", "TOOL_HANDLER_MISSING"}, (
        f"expected TOOL_NOT_IMPLEMENTED or TOOL_HANDLER_MISSING, got {result.code!r}"
    )
    payload = result.payload if isinstance(result.payload, dict) else {}
    assert payload.get("text") != "must-not-succeed-as-echo"
