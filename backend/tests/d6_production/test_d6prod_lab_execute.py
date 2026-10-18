"""D6 FULL-PROD §5 F — Lab execute: BE POST /tools/{id}/execute with SSRF-at-call."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.d6_production.conftest import as_user, make_tool

PRIME = Path(__file__).resolve().parents[2] / "prime_agent"
FE_TOOLS = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "api"
    / "endpoints"
    / "tools.ts"
)


def _fe_claims_execute() -> bool:
    if not FE_TOOLS.is_file():
        return False
    text = FE_TOOLS.read_text(encoding="utf-8")
    return (
        "execute" in text
        and "/execute" in text
        and ("toolsAPI" in text or "export const toolsAPI" in text)
    )


def test_prod_lab_execute_route_or_fe_has_no_claim() -> None:
    """Either BE ships POST /tools/{id}/execute with SSRF-at-call, or FE must not claim it."""
    routes = (PRIME / "web" / "routes" / "tools.py").read_text(encoding="utf-8")
    service = (PRIME / "services" / "tool_service.py").read_text(encoding="utf-8")
    combined = routes + "\n" + service

    be_has_execute = (
        "/execute" in routes
        or "async def execute" in combined
        or "def execute_tool" in combined
        or '"/execute"' in combined
    )

    if _fe_claims_execute() and not be_has_execute:
        pytest.fail(
            "D6 FULL-PROD: FE toolsAPI.execute claims POST /tools/:id/execute but "
            "BE has no execute route — either ship Lab execute with SSRF-at-call "
            "or remove the FE execute claim"
        )

    if be_has_execute:
        # SSRF must be re-checked at call time (not only create).
        assert (
            "_validate_endpoint" in combined
            or "ssrf" in combined.lower()
            or "validate_endpoint" in combined
        ), (
            "D6 FULL-PROD: Lab execute exists but no SSRF re-check at call time"
        )


@pytest.mark.asyncio
async def test_prod_lab_execute_ssrf_at_call(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """If execute is shipped, private/rebound URLs must fail at call time."""
    routes_src = (PRIME / "web" / "routes" / "tools.py").read_text(encoding="utf-8")
    if "/execute" not in routes_src and "execute" not in routes_src:
        if _fe_claims_execute():
            pytest.fail(
                "D6 FULL-PROD: FE claims lab execute; BE route missing "
                "(see test_prod_lab_execute_route_or_fe_has_no_claim)"
            )
        pytest.skip("Lab execute not shipped and FE does not claim it — OK optional path")

    tool = await make_tool(
        db_session,
        owner,
        name="d6prod_lab_exec",
        endpoint="https://api.example.com/ok",
    )
    # Mutate to a blocked target as if DNS rebound after save.
    tool.endpoint = "http://127.0.0.1/secret"
    await db_session.commit()

    async with as_user(db_session, owner) as api:
        resp = await api.post(
            f"/api/v1/tools/{tool.id}/execute",
            json={"parameters": {"q": "x"}},
        )
    assert resp.status_code in {400, 422, 403}, (
        f"D6 FULL-PROD: Lab execute must SSRF-reject loopback at call time; "
        f"got {resp.status_code} {resp.text}"
    )
