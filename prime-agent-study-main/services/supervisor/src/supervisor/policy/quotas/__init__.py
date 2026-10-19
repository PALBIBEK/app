"""Quotas subsystem — tenant resource limits.

Components:
- QuotaEnforcer/TenantQuotaEnforcer: Quota enforcement with atomic checks
- QuotaLimits: Limit configuration
- QuotaExceeded: Exception for quota violations
"""

from supervisor.policy.errors import QuotaExceeded
from supervisor.policy.quotas.enforcer import QuotaEnforcer
from supervisor.policy.quotas.limits import DEFAULT_QUOTA_LIMITS
from supervisor.policy.types import QuotaLimits

# Alias for backwards compatibility
TenantQuotaEnforcer = QuotaEnforcer
TenantQuotaLimits = QuotaLimits

__all__ = [
    "DEFAULT_QUOTA_LIMITS",
    "QuotaEnforcer",
    "QuotaExceeded",
    "QuotaLimits",
    "TenantQuotaEnforcer",
    "TenantQuotaLimits",
]
