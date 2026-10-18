"""D11 T2, T12, T13 — regression pointers + IDOR / re-attach."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.d11_acceptance.conftest import STRANGER, bootstrap_scope, headers_for


@pytest.mark.asyncio
async def test_t2_foreign_scope_create_403(postgres_grant_stack) -> None:
    scope = await bootstrap_scope(postgres_grant_stack)
    client = postgres_grant_stack["client"]

    foreign = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers_for(STRANGER),
    )
    assert foreign.status_code == 403


def test_t12_mass_assignment_regression_pointer() -> None:
    """WP-M7: tool args cannot inject grant/scope_id — see unit suite."""
    path = (
        Path(__file__).resolve().parents[1]
        / "unit"
        / "test_wp_m7_mass_assignment.py"
    )
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "scope:admin" in text
    assert "scope_id" in text


@pytest.mark.asyncio
async def test_t13_existing_scope_not_rebootstrapped(postgres_grant_stack) -> None:
    """Creator without prior membership cannot re-bootstrap an existing scope."""
    grants = postgres_grant_stack["grants"]
    scope = await bootstrap_scope(postgres_grant_stack)

    client = postgres_grant_stack["client"]
    impostor = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers_for("new-creator"),
    )
    assert impostor.status_code == 403
    assert not await grants.is_member("new-creator", scope)
