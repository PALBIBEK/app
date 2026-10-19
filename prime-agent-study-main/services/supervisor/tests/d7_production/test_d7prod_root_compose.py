"""D7 FULL-PROD — root docker-compose.yml includes Memory service (preferred)."""

from __future__ import annotations

import re

from tests.d7_production.conftest import (
    STAGING_COMPOSE,
)


def _has_memory_service(compose_text: str) -> bool:
    return re.search(r"(?m)^\s*memory:\s*$", compose_text) is not None




def test_prod_staging_compose_still_has_memory() -> None:
    """Staging compose remains the reference Memory deploy even when root catches up."""
    assert STAGING_COMPOSE.is_file(), STAGING_COMPOSE
    text = STAGING_COMPOSE.read_text(encoding="utf-8")
    assert _has_memory_service(text), "staging compose lost memory service"
