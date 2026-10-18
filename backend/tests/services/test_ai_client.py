"""AiClient outbound header tests — never leak user access tokens."""

import json

import httpx
import pytest

from prime_agent.config import Settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError


SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


@pytest.mark.asyncio
async def test_ai_client_sends_service_bearer_only_not_user_access_token():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["x_tenant"] = request.headers.get("x-tenant-id", "")
        return httpx.Response(200, json={"status": "ok"})

    settings = Settings(
        environment="development",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret=SECRET,
    )
    client = AiClient(
        settings,
        transport=httpx.MockTransport(handler),
    )
    identity = TenantPrincipal(tenant_id="tenant-a", principal_id="tenant-a")
    body = await client.healthz(identity)

    assert body["status"] == "ok"
    assert captured["authorization"].startswith("Bearer ")
    token = captured["authorization"].removeprefix("Bearer ")
    # User-shaped access tokens are not JWTs with type=access under this secret path —
    # assert the outbound token is a service JWT with tenant claim.
    import jwt

    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="yal-supervisor",
        issuer="prime-agent-api",
    )
    assert payload["type"] == "service"
    assert payload["tenant_id"] == "tenant-a"
    assert "user-access-token-should-not-appear" not in json.dumps(captured)
    assert captured["x_tenant"] == ""


@pytest.mark.asyncio
async def test_ai_client_raises_when_upstream_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    settings = Settings(
        environment="development",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret=SECRET,
    )
    client = AiClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(AiUpstreamError):
        await client.healthz(TenantPrincipal(tenant_id="t", principal_id="t"))
