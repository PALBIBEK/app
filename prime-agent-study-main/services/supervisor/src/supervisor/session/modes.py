"""Session execution modes: sticky vs. stateless-per-turn."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class SessionMode(StrEnum):
    STICKY = "sticky"
    STATELESS_PER_TURN = "stateless_per_turn"


def mode_for_harness(*, requires_sandbox: bool) -> SessionMode:
    """Legacy default mapping from sandbox requirement."""
    if requires_sandbox:
        return SessionMode.STICKY
    return SessionMode.STATELESS_PER_TURN


def mode_for_manifest(manifest: Any) -> SessionMode:
    """Mode comes from the harness manifest, not a caller flag."""
    mode = getattr(manifest, "mode", None)
    if mode == "sticky":
        return SessionMode.STICKY
    if mode == "stateless":
        return SessionMode.STATELESS_PER_TURN
    return mode_for_harness(requires_sandbox=bool(manifest.requires_sandbox))
