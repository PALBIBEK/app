"""Quota limits — configuration and parsing.

Provides default limits and parsing from environment/config.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from supervisor.policy.types import QuotaLimits

__all__ = [
    "DEFAULT_QUOTA_LIMITS",
]


# Default limits (can be overridden via environment)
DEFAULT_QUOTA_LIMITS: Final[QuotaLimits] = QuotaLimits(
    max_sessions_per_tenant=100,
    max_sandboxes_per_tenant=10,
    max_sessions_global=1000,
    max_cost_per_tenant=Decimal("100.00"),
)


