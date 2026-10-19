"""Security layer for sandbox isolation.

This package provides defense-in-depth security:
    - env.py: Environment sanitization (deny-by-default, 30+ provider prefixes)
    - guards.py: Multi-tenant validation + image digest validation (D14)

All guards are called in router.create() before sandbox creation.
"""

from supervisor.sandbox.resolve import PlaceholderImageError
from supervisor.sandbox.security.env import (
    is_allowed_gateway_inject,
    is_denied_env_key,
    sanitize_sandbox_env,
)
from supervisor.sandbox.security.guards import (
    DigestStatus,
    MultiTenantViolation,
    SecurityCheckResult,
    assert_multi_tenant_provider,
    assert_multi_tenant_sandbox_provider,
    is_placeholder_image_digest,
)

__all__ = [
    "DigestStatus",
    "MultiTenantViolation",
    "PlaceholderImageError",
    "SecurityCheckResult",
    "assert_multi_tenant_provider",
    "assert_multi_tenant_sandbox_provider",
    "is_allowed_gateway_inject",
    "is_denied_env_key",
    "is_placeholder_image_digest",
    "sanitize_sandbox_env",
]
