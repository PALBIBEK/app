"""D7 FULL-PROD — root docker-compose.yml includes Memory service (preferred)."""

from __future__ import annotations

import re

from tests.d7_production.conftest import (
    GATEWAY_PORT,
    ROOT_COMPOSE,
    STAGING_COMPOSE,
    STAGING_ONLY_MARKER,
)


def _has_memory_service(compose_text: str) -> bool:
    return re.search(r"(?m)^\s*memory:\s*$", compose_text) is not None


def test_prod_root_compose_includes_memory_service() -> None:
    """Prefer root docker-compose.yml memory service; staging-only marker is last resort.

    Production reopen: local/dev stack must bring Memory up with supervisor, not only
    deploy/staging. Accept STAGING_ONLY_MEMORY.md only if it explicitly documents
    the intentional omission and points at staging compose.
    """
    assert ROOT_COMPOSE.is_file(), f"missing root compose: {ROOT_COMPOSE}"
    root_text = ROOT_COMPOSE.read_text(encoding="utf-8")

    if _has_memory_service(root_text):
        assert "readyz" in root_text or "healthcheck" in root_text
        assert "MEMORY_SERVICE_URL" in root_text or "memory:" in root_text
        # Port must not collide with gateway.
        mem_block = re.search(
            r"(?ms)^  memory:\s*\n(.*?)(?=^  [a-z0-9_-]+:\s*$|\Z)",
            root_text,
        )
        assert mem_block, "could not parse root compose memory service block"
        block = mem_block.group(1)
        ports = re.findall(r"MEMORY_PORT:\s*[\"']?(\d+)", block)
        published = re.findall(r"[\"'](\d+):\d+[\"']", block)
        candidates = {int(p) for p in ports + published}
        if candidates:
            assert GATEWAY_PORT not in candidates, (
                f"D7 FULL-PROD: root Memory port collides with gateway {GATEWAY_PORT}"
            )
        return

    # Escape hatch: explicit staging-only documented marker.
    if STAGING_ONLY_MARKER.is_file():
        marker = STAGING_ONLY_MARKER.read_text(encoding="utf-8")
        assert re.search(r"staging.?only|intentionally omitted|root compose", marker, re.I), (
            "STAGING_ONLY_MEMORY.md must document why root compose omits memory"
        )
        assert STAGING_COMPOSE.is_file() and _has_memory_service(
            STAGING_COMPOSE.read_text(encoding="utf-8")
        ), "marker present but staging compose still missing memory service"
        return

    raise AssertionError(
        "D7 FULL-PROD: root docker-compose.yml missing memory service. "
        "Add memory (preferred, e.g. 8092 + MEMORY_SERVICE_URL on supervisor) "
        "or create deploy/STAGING_ONLY_MEMORY.md documenting staging-only deploy."
    )


def test_prod_staging_compose_still_has_memory() -> None:
    """Staging compose remains the reference Memory deploy even when root catches up."""
    assert STAGING_COMPOSE.is_file(), STAGING_COMPOSE
    text = STAGING_COMPOSE.read_text(encoding="utf-8")
    assert _has_memory_service(text), "staging compose lost memory service"
