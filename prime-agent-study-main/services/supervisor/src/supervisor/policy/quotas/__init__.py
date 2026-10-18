"""Quotas subsystem — tenant resource limits.

Components:
- QuotaEnforcer/TenantQuotaEnforcer: Quota enforcement with atomic checks
- QuotaLimits: Limit configuration
- QuotaExceeded: Exception for quota violations
"""

from supervisor.policy.quotas.enforcer import QuotaEnforcer
from supervisor.policy.quotas.limits import (
    DEFAULT_QUOTA_LIMITS,
    parse_quota_limits,
)
from supervisor.policy.errors import QuotaExceeded

# Alias for backwards compatibility
TenantQuotaEnforcer = QuotaEnforcer
TenantQuotaLimits = DEFAULT_QUOTA_LIMITS

__all__ = [
    "QuotaEnforcer",
    "QuotaExceeded",
    "TenantQuotaEnforcer",
    "TenantQuotaLimits",
    "DEFAULT_QUOTA_LIMITS",
    "parse_quota_limits",
]
