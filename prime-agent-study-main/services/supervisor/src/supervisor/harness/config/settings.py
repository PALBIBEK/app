"""Centralized harness configuration - replaces hardcoded values.

All environment-based configuration for the harness subsystem
is centralized here. Settings are cached for efficiency.

Environment Variables:
    SUPERVISOR_BASE_URL: Base URL for supervisor API (default: http://127.0.0.1:8080)
    HARNESS_CLI: Path to harness CLI executable
    YAL_CONTRACTS_DIR / CONTRACTS_DIR: Path to contracts directory
    MEMORY_SERVICE_URL: URL for durable memory service
    MEMORY_TENANT: Tenant ID for memory service (default: default)
    MEMORY_NAMESPACE: Namespace for memory service (default: facts)
    MEMORY_SUBJECT: Subject for memory service
    MEMORY_LEASE_OWNER: Lease owner for memory service
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from supervisor.harness.storage.validators import get_env_url

__all__ = [
    "HarnessSettings",
    "clear_settings_cache",
    "get_settings",
]


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    """Harness configuration loaded from environment.

    All fields are immutable after creation.

    Attributes:
        supervisor_base_url: Base URL for supervisor API
        harness_cli: Path to harness CLI executable (optional)
        contracts_dir: Path to contracts directory (optional)
        memory_service_url: URL for durable memory service (optional)
        memory_tenant: Tenant ID for memory service
        memory_namespace: Namespace for memory service
        memory_subject: Subject for memory service (default: the session)
        memory_lease_owner: Lease owner for memory service (optional)
        default_protocol_versions: Default supported protocol versions
    """

    supervisor_base_url: str
    harness_cli: str | None
    contracts_dir: str | None
    memory_service_url: str | None
    memory_tenant: str
    memory_namespace: str
    default_protocol_versions: frozenset[int]
    memory_subject: str | None = None
    memory_lease_owner: str | None = None

    @classmethod
    def from_env(cls) -> HarnessSettings:
        """Create settings from environment variables.

        Returns:
            HarnessSettings instance with values from environment
        """
        # Supervisor base URL
        supervisor_base_url = get_env_url(
            "SUPERVISOR_BASE_URL",
            "http://127.0.0.1:8080",
        )

        # Harness CLI path
        harness_cli = os.environ.get("HARNESS_CLI", "").strip() or None

        # Contracts directory (try multiple env vars)
        contracts_dir = (
            os.environ.get("YAL_CONTRACTS_DIR", "").strip()
            or os.environ.get("CONTRACTS_DIR", "").strip()
            or None
        )

        # Memory service settings
        memory_service_url = os.environ.get("MEMORY_SERVICE_URL", "").strip() or None
        if memory_service_url:
            memory_service_url = memory_service_url.rstrip("/")

        memory_tenant = os.environ.get("MEMORY_TENANT", "default").strip()
        memory_namespace = os.environ.get("MEMORY_NAMESPACE", "facts").strip()

        return cls(
            supervisor_base_url=supervisor_base_url,
            harness_cli=harness_cli,
            contracts_dir=contracts_dir,
            memory_service_url=memory_service_url,
            memory_tenant=memory_tenant,
            memory_namespace=memory_namespace,
            default_protocol_versions=frozenset({1, 2}),
            memory_subject=os.environ.get("MEMORY_SUBJECT", "").strip() or None,
            memory_lease_owner=os.environ.get("MEMORY_LEASE_OWNER", "").strip() or None,
        )

    def get_contracts_path(self) -> Path | None:
        """Get contracts directory path if configured.

        Returns:
            Path to contracts directory or None
        """
        if self.contracts_dir:
            return Path(self.contracts_dir)
        return None

    def get_protocol_versions_path(self) -> Path | None:
        """Get path to protocol_versions.yaml if contracts dir is configured.

        Returns:
            Path to protocol_versions.yaml or None
        """
        contracts = self.get_contracts_path()
        if contracts:
            return contracts / "protocol_versions.yaml"
        return None


@lru_cache(maxsize=1)
def get_settings() -> HarnessSettings:
    """Get cached settings instance.

    Settings are loaded once and cached. Use clear_settings_cache()
    to force reload (useful in tests).

    Returns:
        Cached HarnessSettings instance
    """
    return HarnessSettings.from_env()


def clear_settings_cache() -> None:
    """Clear the settings cache.

    Useful in tests to reset settings between test cases.
    """
    get_settings.cache_clear()
