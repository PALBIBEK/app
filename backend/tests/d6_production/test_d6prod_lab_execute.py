"""D6 FULL-PROD §5 F — Lab execute: BE POST /tools/{id}/execute with SSRF-at-call."""

from __future__ import annotations

from pathlib import Path

import pytest

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
