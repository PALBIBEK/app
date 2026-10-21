"""Harness configuration from the environment, cached.

Environment Variables:
    YAL_CONTRACTS_DIR / CONTRACTS_DIR: Path to contracts directory (protocol versions)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "HarnessSettings",
    "clear_settings_cache",
    "get_settings",
]


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    """Harness configuration loaded from environment.

    Attributes:
        contracts_dir: Path to contracts directory (optional)
        default_protocol_versions: Default supported protocol versions
    """

    contracts_dir: str | None
    default_protocol_versions: frozenset[int]

    @classmethod
    def from_env(cls) -> HarnessSettings:
        contracts_dir = (
            os.environ.get("YAL_CONTRACTS_DIR", "").strip()
            or os.environ.get("CONTRACTS_DIR", "").strip()
            or None
        )
        return cls(contracts_dir=contracts_dir, default_protocol_versions=frozenset({1, 2}))




@lru_cache(maxsize=1)
def get_settings() -> HarnessSettings:
    """Cached settings; ``clear_settings_cache()`` reloads them (tests)."""
    return HarnessSettings.from_env()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
