"""Security layer for sandbox isolation.

This package provides defense-in-depth security:
    - env.py: Environment sanitization (deny-by-default, 30+ provider prefixes)
    - guards.py: Multi-tenant validation + image digest validation (D14)

All guards are called in router.create() before sandbox creation.
"""

from supervisor.sandbox.security.env import (
    EnvSanitizer,
    is_allowed_gateway_inject,
    is_denied_env_key,
    sanitize_sandbox_env,
)
from supervisor.sandbox.security.guards import (
    DigestStatus,
    ImageDigestError,
    MultiTenantViolation,
    SecurityCheckResult,
    assert_multi_tenant_provider,
    assert_multi_tenant_sandbox_provider,
    is_placeholder_image_digest,
    validate_image_digest,
)
from supervisor.sandbox.resolve import PlaceholderImageError

__all__ = [
    # Environment
    "sanitize_sandbox_env",
    "is_denied_env_key",
    "is_allowed_gateway_inject",
    "EnvSanitizer",
    # Guards
    "assert_multi_tenant_provider",
    "assert_multi_tenant_sandbox_provider",
    "is_placeholder_image_digest",
    "validate_image_digest",
    "DigestStatus",
    "SecurityCheckResult",
    "MultiTenantViolation",
    "ImageDigestError",
    "PlaceholderImageError",
]
