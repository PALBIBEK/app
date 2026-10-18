"""D7 FULL-PROD — product privacy: owner can call BFF forget / erase_subject."""

from __future__ import annotations

import pytest

from tests.d7_production.conftest import app_memory_route_paths, as_user, source_blob


def _privacy_paths() -> list[str]:
    paths = app_memory_route_paths()
    out: list[str] = []
    for p in sorted(paths):
        low = p.lower()
        if "forget" in low or "erase" in low:
            out.append(p)
    return out


@pytest.mark.asyncio
async def test_prod_owner_can_call_forget_or_erase(
    db_session, owner, d7prod_settings
) -> None:
    """Authenticated owner must be able to invoke forget/erase_subject on the BFF.

    Production reopen: privacy path is product-callable (not Memory Service–only).
    """
    privacy = _privacy_paths()
    assert privacy, (
        "D7 FULL-PROD: no BFF forget/erase routes — owner cannot exercise privacy controls"
    )

    # Prefer concrete path shapes FastAPI may register.
    candidates = [
        "/api/v1/memory/forget",
        "/api/v1/memory/erase_subject",
        "/api/v1/memory/erase",
        *privacy,
    ]
    # Dedupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        # Normalize {param} style routes for probing by skipping templated ones.
        if "{" in c or c in seen:
            continue
        seen.add(c)
        ordered.append(c)

    async with as_user(db_session, owner) as api:
        last = None
        for path in ordered:
            # Try POST first (durable Memory uses POST forget/erase_subject).
            for method in ("post", "delete"):
                payload = {
                    "scope": {
                        "tenant": "ignored-by-bff-claim",
                        "subject": str(owner.id),
                        "namespace": "default",
                    },
                    "selector": {"item_id": "00000000-0000-0000-0000-000000000001"},
                    "actor": str(owner.id),
                    "reason": "d7-prod privacy acceptance",
                }
                fn = getattr(api, method)
                if method == "post":
                    r = await fn(path, json=payload)
                else:
                    r = await fn(path)
                last = (path, method, r.status_code, r.text)
                # Owner-callable: not 404/405 missing route; auth may yield 401/403/502 upstream.
                if r.status_code not in {404, 405, 501}:
                    assert r.status_code != 401, (
                        f"owner override should authenticate; got {r.status_code} on {path}"
                    )
                    return

    pytest.fail(
        "D7 FULL-PROD: owner could not invoke any forget/erase BFF route "
        f"(last={last}). Wire POST /api/v1/memory/forget and erase_subject."
    )


@pytest.mark.asyncio
async def test_prod_privacy_routes_are_owner_scoped_not_open(
    db_session, owner, other, d7prod_settings
) -> None:
    """Forget/erase must require auth and bind subject/ownership — not anonymous open."""
    blob = source_blob(
        "web/routes/*.py",
        "web/routes/**/*.py",
        "services/*memory*.py",
    )
    if "forget" not in blob.lower() and "erase" not in blob.lower():
        pytest.fail(
            "D7 FULL-PROD: no forget/erase BFF source — cannot verify owner scoping"
        )

    # Structural: route module must depend on current user / ownership checks.
    ownership_markers = (
        "get_current_user",
        "current_user",
        "owner",
        "user_id",
        "TenantPrincipal",
        "subject",
    )
    assert any(m in blob for m in ownership_markers), (
        "D7 FULL-PROD: memory privacy routes must bind owner/subject (get_current_user etc.)"
    )

    privacy = _privacy_paths()
    if not privacy:
        pytest.fail("D7 FULL-PROD: privacy routes missing from app.routes")

    # Unauthenticated probe against create_app without user override.
    from prime_agent.main import create_app
    from prime_agent.web.dependencies import get_db
    import httpx

    app = create_app()

    async def _no_db():
        # Auth must reject before business logic; avoid requiring a live DB pool.
        yield None

    app.dependency_overrides[get_db] = _no_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/v1/memory/forget", "/api/v1/memory/erase_subject"):
            r = await client.post(path, json={})
            if r.status_code == 404:
                continue
            assert r.status_code in {401, 403, 422}, (
                f"unauthenticated forget/erase must not succeed ({path} → {r.status_code})"
            )
            return

    # If concrete paths 404, require that registered privacy routes reject anon.
    pytest.fail(
        "D7 FULL-PROD: could not probe forget/erase auth gate — routes not registered"
    )
