"""D6 FULL-PROD — custom endpoint → http_url/MCP bind; no silent echo for orphans."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import pytest

from tests.d6_production.conftest import make_tool, patch_ai_transport


def _load_publish():
    mod = importlib.import_module("prime_agent.services.tool_publish_service")
    fn = getattr(mod, "publish_owned_tool_uuids", None)
    assert callable(fn), "publish_owned_tool_uuids required"
    return mod, fn


@pytest.mark.asyncio
async def test_prod_custom_with_endpoint_bind_requires_http_or_mcp(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """Custom tool WITH endpoint: bind succeeds only when HttpTool (http_url) or MCP registered.

    Publish body must embed ``http_url`` (or mcp registration) — not echo-shaped defs.
    """
    _, publish_fn = _load_publish()
    tool = await make_tool(
        db_session,
        owner,
        name="d6prod_http_search",
        category="custom",
        endpoint="https://api.example.com/search",
    )

    published_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "tool_set/publish" in str(request.url.path):
            body = json.loads(request.content.decode())
            published_bodies.append(body)
            return httpx.Response(
                200,
                json={
                    "kind": "tool_set",
                    "name": body.get("name", "derived"),
                    "digest": "sha256:" + "b" * 64,
                    "semver": "1.0.0",
                    "status": "offered",
                },
            )
        return httpx.Response(200, json={"ready": True, "run_allowed": True})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    from prime_agent.schemas.ai_identity import TenantPrincipal

    identity = TenantPrincipal(tenant_id=str(owner.id), principal_id=str(owner.id))
    result = await publish_fn(
        db=db_session,
        user_id=owner.id,
        identity=identity,
        tool_ids=[tool.id],
        settings=d6_settings,
        require_handlers=True,
    )
    assert published_bodies, "expected POST /v1/catalog/tool_set/publish"
    body = published_bodies[0]
    defs = body.get("definitions") or {}
    custom_def = defs.get(tool.name) or {}
    assert custom_def.get("http_url") == tool.endpoint or custom_def.get(
        "tool_class"
    ) == "http", (
        "D6 FULL-PROD: custom+endpoint bind must register HttpTool via http_url "
        f"(or MCP); got definition={custom_def!r} result={result!r}"
    )
    # Must not coerce to echo-only schema without http_url.
    props = (custom_def.get("parameters") or {}).get("properties") or {}
    assert custom_def.get("http_url") or custom_def.get("mcp_server_url"), (
        "missing HttpTool/MCP registration on published custom tool"
    )
    assert not (
        set(props.keys()) <= {"text"} and not custom_def.get("http_url")
    ), "echo-shaped publish without http_url is forbidden"


@pytest.mark.asyncio
async def test_prod_custom_without_endpoint_no_silent_echo_allow(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """Arbitrary custom name WITHOUT endpoint → TOOL_HANDLER_MISSING at bind."""
    _, publish_fn = _load_publish()
    tool = await make_tool(
        db_session,
        owner,
        name="d6prod_orphan_no_endpoint",
        category="custom",
        endpoint=None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ready": True})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    from prime_agent.schemas.ai_identity import TenantPrincipal
    from prime_agent.services.tool_publish_service import _tool_has_handler

    assert _tool_has_handler(tool) is False, (
        "D6 FULL-PROD: _tool_has_handler must be False for custom without endpoint "
        "and without explicit HttpTool/MCP/builtin registration (no silent echo allow)"
    )

    identity = TenantPrincipal(tenant_id=str(owner.id), principal_id=str(owner.id))
    with pytest.raises(Exception) as excinfo:
        await publish_fn(
            db=db_session,
            user_id=owner.id,
            identity=identity,
            tool_ids=[tool.id],
            settings=d6_settings,
            require_handlers=True,
        )
    code = getattr(excinfo.value, "error_code", None) or str(excinfo.value)
    assert "TOOL_HANDLER_MISSING" in str(code), (
        f"expected TOOL_HANDLER_MISSING at bind, got {excinfo.value!r}"
    )


def test_prod_definition_for_tool_does_not_invent_echo_schema() -> None:
    """Publishing must not coerce empty custom schemas into echo {text} shape."""
    mod = importlib.import_module("prime_agent.services.tool_publish_service")
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "echo-shaped" not in src.lower() and "echo-compatible" not in src.lower(), (
        "D6 FULL-PROD: tool_publish_service still documents/implements echo-shaped "
        "fallback for custom tools — remove for production bind"
    )
    # Empty-params custom must not hardcode inventing only {"text": ...} without http_url.
    assert 'properties": {"text"' not in src.replace(" ", "") or "http_url" in src, (
        "D6 FULL-PROD: suspicious echo text property coercion without http_url path"
    )
