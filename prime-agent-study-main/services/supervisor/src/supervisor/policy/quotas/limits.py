"""Quota limits — configuration and parsing.

Provides default limits and parsing from environment/config.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Final

from supervisor.policy.types import QuotaLimits


__all__ = [
    "DEFAULT_QUOTA_LIMITS",
    "parse_quota_limits",
    "limits_from_env",
]


# Default limits (can be overridden via environment)
DEFAULT_QUOTA_LIMITS: Final[QuotaLimits] = QuotaLimits(
    max_sessions_per_tenant=100,
    max_sandboxes_per_tenant=10,
    max_sessions_global=1000,
    max_cost_per_tenant=Decimal("100.00"),
)


def parse_quota_limits(config: dict[str, Any]) -> QuotaLimits:
    """Parse quota limits from configuration dict.

    Args:
        config: Configuration dictionary with optional keys:
            - max_sessions_per_tenant: int
            - max_sandboxes_per_tenant: int
            - max_sessions_global: int
            - max_cost_per_tenant: float/str/Decimal

    Returns:
        QuotaLimits instance
    """
    max_sessions_per_tenant = config.get("max_sessions_per_tenant")
    max_sandboxes_per_tenant = config.get("max_sandboxes_per_tenant")
    max_sessions_global = config.get("max_sessions_global")
    max_cost_per_tenant = config.get("max_cost_per_tenant")

    # Parse cost to Decimal
    if max_cost_per_tenant is not None:
        if isinstance(max_cost_per_tenant, (int, float)):
            max_cost_per_tenant = Decimal(str(max_cost_per_tenant))
        elif isinstance(max_cost_per_tenant, str):
            max_cost_per_tenant = Decimal(max_cost_per_tenant)

    return QuotaLimits(
        max_sessions_per_tenant=max_sessions_per_tenant,
        max_sandboxes_per_tenant=max_sandboxes_per_tenant,
        max_sessions_global=max_sessions_global,
        max_cost_per_tenant=max_cost_per_tenant,
    )


def limits_from_env() -> QuotaLimits:
    """Load quota limits from environment variables.

    Environment variables:
        - QUOTA_MAX_SESSIONS_PER_TENANT
        - QUOTA_MAX_SANDBOXES_PER_TENANT
        - QUOTA_MAX_SESSIONS_GLOBAL
        - QUOTA_MAX_COST_PER_TENANT

    Returns:
        QuotaLimits from environment (falls back to defaults)
    """
    config: dict[str, Any] = {}

    if (val := os.environ.get("QUOTA_MAX_SESSIONS_PER_TENANT")):
        config["max_sessions_per_tenant"] = int(val)

    if (val := os.environ.get("QUOTA_MAX_SANDBOXES_PER_TENANT")):
        config["max_sandboxes_per_tenant"] = int(val)

    if (val := os.environ.get("QUOTA_MAX_SESSIONS_GLOBAL")):
        config["max_sessions_global"] = int(val)

    if (val := os.environ.get("QUOTA_MAX_COST_PER_TENANT")):
        config["max_cost_per_tenant"] = val

    # Merge with defaults
    defaults = DEFAULT_QUOTA_LIMITS
    return QuotaLimits(
        max_sessions_per_tenant=config.get("max_sessions_per_tenant", defaults.max_sessions_per_tenant),
        max_sandboxes_per_tenant=config.get("max_sandboxes_per_tenant", defaults.max_sandboxes_per_tenant),
        max_sessions_global=config.get("max_sessions_global", defaults.max_sessions_global),
        max_cost_per_tenant=(
            parse_quota_limits(config).max_cost_per_tenant
            if "max_cost_per_tenant" in config
            else defaults.max_cost_per_tenant
        ),
    )
