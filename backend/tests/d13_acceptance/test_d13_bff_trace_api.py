"""D13 §5 C / §8 T7 — Trace API (or Jaeger proxy) tenant-scoped when claimed."""

from __future__ import annotations

from pathlib import Path

import pytest

from prime_agent.services.ai_client import AiClient
from tests.d6_acceptance.conftest import as_user

PRIME = Path(__file__).resolve().parents[2] / "prime_agent"


def _routes_src() -> str:
    routes = PRIME / "web" / "routes"
    combined = ""
    for path in sorted(routes.glob("*.py")):
        combined += path.read_text(encoding="utf-8") + "\n"
    return combined


def test_trace_api_routes_or_ai_client_proxy_exist() -> None:
    """Milestone C: GET /traces and /traces/{id}/spans (or Jaeger proxy) via BFF."""
    src = Path(
        __import__("prime_agent.services.ai_client", fromlist=["*"]).__file__
    ).read_text(encoding="utf-8")
    routes = _routes_src()
    combined = src + "\n" + routes

    has_list = (
        hasattr(AiClient, "list_traces")
        or hasattr(AiClient, "get_traces")
        or "/traces" in src
        or (
            "/traces" in routes
            and ("APIRouter" in routes or "@router" in routes or "def " in routes)
        )
    )
    has_spans = (
        hasattr(AiClient, "get_trace_spans")
        or hasattr(AiClient, "list_trace_spans")
        or "/spans" in combined
        or "traces/{trace_id}" in combined
        or "traces/{id}" in combined
    )
    assert has_list, (
        "BFF Trace API missing: need AiClient list_traces (or /traces route) — D13 §5 C"
    )
    assert has_spans, (
        "BFF Trace API missing: need GET /traces/{trace_id}/spans (or Jaeger proxy) — D13 §5 C"
    )


@pytest.mark.asyncio
async def test_t7_trace_api_wrong_tenant_denied(
    db_session, owner, other_user
) -> None:
    """T7: Trace API wrong tenant → 404/403 (never leak foreign spans)."""
    routes = _routes_src()
    assert "/traces" in routes or hasattr(AiClient, "list_traces"), (
        "Trace API routes not registered — cannot satisfy T7 until D13 §5 C lands"
    )

    # Probe registered BFF paths as owner, then as foreign user.
    probes = (
        "/api/v1/traces",
        "/api/traces",
        "/traces",
        "/api/v1/ai/traces",
    )
    hit_path = None
    async with as_user(db_session, owner) as client:
        for path in probes:
            r = await client.get(path)
            detail = None
            if r.headers.get("content-type", "").startswith("application/json"):
                try:
                    detail = r.json().get("detail")
                except Exception:
                    detail = None
            # Starlette bare "Not Found" means route missing; keep probing.
            if r.status_code == 404 and detail in ("Not Found", None):
                continue
            hit_path = path
            break
        if hit_path is None:
            pytest.fail(
                "D13 T7: no BFF /traces route registered (all probes 404 Not Found)"
            )

    async with as_user(db_session, other_user) as client:
        r = await client.get(hit_path)
        assert r.status_code in (401, 403, 404), (
            f"wrong tenant must be denied for {hit_path}, got {r.status_code}: {r.text}"
        )
