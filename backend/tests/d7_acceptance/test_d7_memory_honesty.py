"""D7 §5 D / T14 — outer Memory honesty (422 blocked or live durable BFF)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.d7_acceptance.conftest import as_user

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PRIME_AGENT = BACKEND_ROOT / "prime_agent"


@pytest.mark.asyncio
async def test_t14_harness_memory_attach_422_or_live_bff(
    db_session, owner, d7_settings
) -> None:
    """§5 D / T14: non-empty harness.memory → 422 honesty OR durable BFF exists.

    Theater = silently accepting mock document-store IDs without durable proxy.
    """
    async with as_user(db_session, owner) as api:
        # Create harness with memory store IDs attached.
        r = await api.post(
            "/api/v1/harnesses",
            json={
                "name": "d7-memory-harness",
                "model": "claude-sonnet-4",
                "mode": "sticky",
                "tools": [],
                "memory": ["mem_fake_document_store"],
            },
        )
    # Honest blocked path:
    if r.status_code == 422:
        detail = r.json().get("detail", "")
        assert "not available" in str(detail).lower() or "memory" in str(detail).lower()
        return

    # Live BFF path: must not 201-success a fake store without durable wiring.
    if r.status_code in {200, 201}:
        # Require an explicit durable/memory proxy module or route.
        routes = PRIME_AGENT / "web" / "routes"
        combined = ""
        for path in routes.glob("*.py"):
            combined += path.read_text(encoding="utf-8")
        services = PRIME_AGENT / "services"
        for path in services.glob("*memory*"):
            if path.suffix == ".py":
                combined += path.read_text(encoding="utf-8")
        has_durable_proxy = (
            "/v1/memory/" in combined
            or "MEMORY_SERVICE" in combined
            or "durable" in combined.lower()
        )
        assert has_durable_proxy, (
            "D7 T14: harness accepted memory[] but no durable Memory BFF proxy found — "
            "either keep 422 'Memory stores are not available yet' or wire BFF→durable"
        )
        return

    pytest.fail(f"unexpected harness create status {r.status_code}: {r.text}")


def test_t14_no_silent_mock_memory_crud_routes_without_durable() -> None:
    """If BFF exposes /memory CRUD, it must proxy durable APIs — not a local fake table."""
    routes_dir = PRIME_AGENT / "web" / "routes"
    memory_routes = list(routes_dir.glob("*memory*")) if routes_dir.is_dir() else []
    if not memory_routes:
        # No memory routes — honesty via harness 422 is OK (paired with FE not-theater).
        return
    combined = "\n".join(p.read_text(encoding="utf-8") for p in memory_routes)
    assert "/v1/memory/" in combined or "MEMORY_SERVICE" in combined, (
        "D7: outer /memory routes exist but do not proxy Durable Memory Service"
    )


def test_supervisor_bff_does_not_import_parallel_memory_workspace_as_ltm() -> None:
    """Name trap: MemoryWorkspaceStore is workspace backend — BFF must not treat as LTM."""
    # AiClient / harness_service should not call create_grant_store("memory") as LTM.
    hs = (PRIME_AGENT / "services" / "harness_service.py").read_text(encoding="utf-8")
    assert 'create_grant_store("memory")' not in hs
    assert "MemoryWorkspaceStore" not in hs
