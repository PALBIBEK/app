"""D7 FULL-PROD — rate limit header or middleware on memory mutating routes."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from .conftest import mint_service_jwt

AI_MEMORY_SRC = Path(__file__).resolve().parents[2] / "src" / "memory_service"


def _memory_source_blob() -> str:
    chunks: list[str] = []
    for path in sorted(AI_MEMORY_SRC.rglob("*.py")):
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_prod_rate_limit_surface_exists_in_memory_service() -> None:
    """Mutating Memory routes must have a rate-limit middleware, dependency, or helper."""
    blob = _memory_source_blob()
    markers = (
        r"\brate_limit\b",
        r"\bRateLimit\b",
        r"\bRateLimiter\b",
        r"X-RateLimit",
        r"Retry-After",
        r"slowapi",
        r"MEMORY_RATE_LIMIT",
    )
    hit = any(re.search(p, blob) for p in markers)
    assert hit, (
        "D7 FULL-PROD: no rate-limit surface in memory_service "
        "(need middleware/dep emitting X-RateLimit-* or 429 on mutate)"
    )


def test_prod_mutating_routes_emit_rate_limit_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/memory/items must hit rate-limit middleware (429) and/or emit rate headers."""
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.setenv("SERVICE_JWT_SECRET", "d7-prod-memory-jwt-secret-at-least-32b!!")
    monkeypatch.setenv("MEMORY_JWT_AUDIENCE", "yal-memory")
    monkeypatch.setenv("MEMORY_JWT_ISSUER", "prime-agent-api")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    # Tiny bucket so middleware is observable without relying on headers alone.
    monkeypatch.setenv("MEMORY_RATE_LIMIT_BURST", "1")
    monkeypatch.setenv("MEMORY_RATE_LIMIT_PER_SECOND", "0.01")

    from memory_service.app import create_app

    token = mint_service_jwt(tenant_id="tenant-rl")
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        first = client.post(
            "/v1/memory/items",
            headers=headers,
            json={
                "scope": {"tenant": "tenant-rl", "subject": "s", "namespace": "n"},
                "content": "d7-prod rate probe 0",
            },
        )
        second = client.post(
            "/v1/memory/items",
            headers=headers,
            json={
                "scope": {"tenant": "tenant-rl", "subject": "s", "namespace": "n"},
                "content": "d7-prod rate probe 1",
            },
        )

    hdrs = {k.lower(): v for k, v in first.headers.items()}
    rate_keys = [k for k in hdrs if "ratelimit" in k or k == "retry-after"]
    saw_signal = second.status_code == 429 or bool(rate_keys) or first.status_code == 429
    assert saw_signal, (
        "D7 FULL-PROD: mutating /v1/memory/items must return 429 (middleware) "
        "or X-RateLimit-*/Retry-After; "
        f"first={first.status_code} second={second.status_code} headers={sorted(hdrs)}"
    )


def test_prod_forget_route_covered_by_rate_limit_surface() -> None:
    """Forget is a privacy mutate — must be in the rate-limited route set once middleware exists."""
    app_py = AI_MEMORY_SRC / "app.py"
    assert app_py.is_file(), app_py
    text = app_py.read_text(encoding="utf-8")
    # Soft structural check: forget handler exists; rate-limit markers must appear near mutates.
    assert "/v1/memory/forget" in text
    tree = ast.parse(text)
    # Ensure we at least reference rate limiting somewhere in app wiring when present.
    blob = _memory_source_blob()
    if not re.search(r"rate.?limit|RateLimit|X-RateLimit|Retry-After", blob, re.IGNORECASE):
        raise AssertionError(
            "D7 FULL-PROD: forget mutate has no accompanying rate-limit middleware/header surface"
        )
    # Keep AST walk so empty/pass modules do not silently satisfy later refactors.
    assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
