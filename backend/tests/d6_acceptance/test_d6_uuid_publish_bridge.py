"""D6 §5 C / T10–T11 — system UUID → ToolSet publish/bind; unowned rejected."""

from __future__ import annotations

import importlib
from uuid import uuid4

import httpx
import pytest

from tests.d6_acceptance.conftest import as_user, make_tool, patch_ai_transport


def _load_publish_api():
    """D6 expected surface: publish owned tool UUIDs into a catalog ToolSet body."""
    candidates = (
        "prime_agent.services.tool_publish_service",
        "prime_agent.services.tool_set_publish",
        "prime_agent.services.harness_tool_publish",
    )
    last_err: Exception | None = None
    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            last_err = exc
            continue
        fn = (
            getattr(mod, "publish_owned_tool_uuids", None)
            or getattr(mod, "publish_harness_tools", None)
            or getattr(mod, "derive_and_publish_tool_set", None)
        )
        if callable(fn):
            return mod, fn
    pytest.fail(
        "D6 §5 C / T10: missing tool publish service "
        f"(tried {candidates}); last import error={last_err!r}. "
        "Implement publish_owned_tool_uuids(owned Tool[] → POST /v1/catalog/tool_set/publish)."
    )


def test_t10_ai_client_can_publish_tool_set() -> None:
    """§5 C: AiClient must expose catalog tool_set publish (system→AI bridge)."""
    from prime_agent.services.ai_client import AiClient

    assert hasattr(AiClient, "publish_tool_set") or hasattr(
        AiClient, "publish_catalog_artifact"
    ), (
        "AiClient needs publish_tool_set / publish_catalog_artifact for "
        "POST /v1/catalog/tool_set/publish"
    )


@pytest.mark.asyncio
async def test_t10_owned_uuids_publish_names_only(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """T10: publish/bind ToolSet contains owned tool names only."""
    _, publish_fn = _load_publish_api()
    owned = await make_tool(db_session, owner, name="owned_demo_tool")
    foreign = await make_tool(db_session, other_user, name="foreign_tool")

    published_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/v1/catalog/tool_set/publish"):
            import json

            body = json.loads(request.content.decode())
            published_bodies.append(body)
            return httpx.Response(
                200,
                json={
                    "kind": "tool_set",
                    "name": body.get("name", "derived"),
                    "digest": "sha256:" + "a" * 64,
                    "semver": body.get("semver", "1.0.0"),
                    "status": "offered",
                },
            )
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "reason": None}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    # Also patch wherever publish service constructs AiClient.
    def make_client(s=None):
        from prime_agent.services.ai_client import AiClient

        return AiClient(s or d6_settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "prime_agent.services.ai_client.AiClient.from_settings",
        make_client,
        raising=False,
    )

    from prime_agent.schemas.ai_identity import TenantPrincipal

    identity = TenantPrincipal(tenant_id=str(owner.id), principal_id=str(owner.id))
    result = await publish_fn(
        db=db_session,
        user_id=owner.id,
        identity=identity,
        tool_ids=[owned.id],
        settings=d6_settings,
    )
    # Result may be digest string, ToolSet summary, or harness update — require names.
    assert published_bodies, "expected POST /v1/catalog/tool_set/publish"
    body = published_bodies[0]
    defs = body.get("definitions") or {}
    tools_map = body.get("tools") or {}
    names = set(defs.keys()) | set(tools_map.keys())
    if not names and "name" in body:
        # Some shapes nest under tools list
        for item in body.get("tools") or []:
            if isinstance(item, dict) and item.get("name"):
                names.add(item["name"])
    assert owned.name in names or owned.name == body.get("name") or (
        isinstance(result, dict) and owned.name in str(result)
    ), f"owned tool name missing from publish body={body} result={result}"
    assert foreign.name not in names


@pytest.mark.asyncio
async def test_t10_unowned_uuid_rejected_at_publish(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """T10/C: unowned UUID rejected at publish/bind (not silently omitted)."""
    _, publish_fn = _load_publish_api()
    foreign = await make_tool(db_session, other_user, name="not_yours")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "reason": None}
            )
        return httpx.Response(500, json={"detail": "must not publish"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    from prime_agent.schemas.ai_identity import TenantPrincipal

    identity = TenantPrincipal(tenant_id=str(owner.id), principal_id=str(owner.id))
    with pytest.raises(Exception) as excinfo:
        await publish_fn(
            db=db_session,
            user_id=owner.id,
            identity=identity,
            tool_ids=[foreign.id],
            settings=d6_settings,
        )
    # 404 / 403 / domain error — must not succeed.
    text = str(excinfo.value).lower()
    assert excinfo.value.__class__.__name__ != "NoneType"
    assert (
        "not found" in text
        or "unowned" in text
        or "forbidden" in text
        or "owner" in text
        or getattr(excinfo.value, "status_code", None) in {403, 404, 422}
        or getattr(excinfo.value, "http_status", None) in {403, 404, 422}
    ), f"unowned UUID must be rejected clearly; got {excinfo.value!r}"


@pytest.mark.asyncio
async def test_t11_custom_without_handler_fails_loud(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """T11: custom tool without endpoint/MCP registration fails bind with clear code."""
    _, publish_fn = _load_publish_api()
    # Custom with no endpoint and no supervisor handler registration.
    tool = await make_tool(
        db_session,
        owner,
        name="orphan_custom_no_endpoint",
        category="custom",
        endpoint=None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ready": True})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    from prime_agent.schemas.ai_identity import TenantPrincipal

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
    err = excinfo.value
    code = (
        getattr(err, "error_code", None)
        or (err.to_dict().get("error_code") if hasattr(err, "to_dict") else None)
        or str(err)
    )
    assert "TOOL_HANDLER_MISSING" in str(code) or "TOOL_HANDLER_MISSING" in str(err), (
        f"expected TOOL_HANDLER_MISSING, got {err!r}"
    )


@pytest.mark.asyncio
async def test_c_harness_save_still_rejects_unowned_tool_ids(
    db_session, owner, other_user
) -> None:
    """Baseline: harness create/update rejects tools the user does not own."""
    foreign = await make_tool(db_session, other_user, name="foreign_on_harness")
    async with as_user(db_session, owner) as api:
        r = await api.post(
            "/api/v1/harnesses",
            json={
                "name": f"d6-bad-tools-{uuid4().hex[:6]}",
                "model": "claude-sonnet-4",
                "mode": "sticky",
                "tools": [foreign.id],
            },
        )
    assert r.status_code in {404, 400, 422}, r.text
