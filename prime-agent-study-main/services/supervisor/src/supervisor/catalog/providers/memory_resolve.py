"""Load pinned MemoryProfile from session pins."""

from __future__ import annotations

from supervisor.catalog.core.types import MemoryProfile, SessionPins
from supervisor.catalog.storage import CompositionCatalog


def load_pinned_memory_profile(
    pins: SessionPins,
    catalog: CompositionCatalog,
) -> MemoryProfile | None:
    """Load offered MemoryProfile from session pins, or None if unpinned."""
    if not pins.memory_profile_id or not pins.memory_profile_digest:
        return None
    art = catalog.get(
        "memory_profile",
        pins.memory_profile_id,
        pins.memory_profile_digest,
        require_offered=True,
    )
    assert isinstance(art, MemoryProfile)
    return art
