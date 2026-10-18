"""Configuration layer for session management.

Provides centralized settings loaded from environment variables
with sensible defaults.
"""

from __future__ import annotations

from supervisor.session.config.settings import (
    SessionSettings,
    get_settings,
)

__all__ = [
    "SessionSettings",
    "get_settings",
]
