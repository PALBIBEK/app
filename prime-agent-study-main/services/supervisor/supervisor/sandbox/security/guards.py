"""Sandbox security guards — policy enforcement for multi-tenant deployments.

This module consolidates security checks that gate sandbox creation:
    1. Multi-tenant provider restrictions (no local providers in strict mode)
    2. Image digest validation (reject placeholders and fixtures)

Design Philosophy:
    - Fail fast with clear error messages
    - Defense in depth (multiple validation layers)
    - Zero false negatives (never allow unsafe configs)

Reference:
    - D14: Placeholder image digest rejection
    - Multi-tenant strict mode enforcement
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final

__all__ = [
    "HostSandboxRefused",
    "MultiTenantViolation",
    "SecurityCheckResult",
    "assert_host_sandbox_allowed",
    "assert_multi_tenant_provider",
    "host_sandbox_allowed",
    "is_placeholder_image_digest",
]


# =============================================================================
# Errors
# =============================================================================

class MultiTenantViolation(ValueError):
    """Raised when provider selection violates multi-tenant policy.

    In strict multi-tenant mode, only fully-isolated providers (e2b, none)
    are permitted. Local providers share host resources and cannot provide
    tenant isolation.
    """

    retryable = False  # a policy answer: the same request is refused again

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"provider {provider!r} is not permitted in MULTI_TENANT_STRICT mode; "
            f"use 'e2b' for isolation or 'none' for no-sandbox harnesses"
        )
        self.provider = provider


# =============================================================================
# Multi-Tenant Provider Guard
# =============================================================================

# Providers that cannot provide tenant isolation
_UNSAFE_PROVIDERS: Final[frozenset[str]] = frozenset({
    "local_process",
    "local_docker",
    "local",
    "process",
    "docker",
})

# Providers safe for multi-tenant deployment
_SAFE_PROVIDERS: Final[frozenset[str]] = frozenset({
    "e2b",
    "none",
    "firecracker",  # Future: VM-based isolation
})


_HOST_PROVIDERS: Final[frozenset[str]] = frozenset({"local_process", "local", "process"})
"""Providers that run the agent's commands as host processes: no isolation at all."""


class HostSandboxRefused(MultiTenantViolation):
    """A host-process sandbox in production or staging, without an operator's opt-in.

    Its commands run as the supervisor's user, beside the supervisor: they can read
    its secrets through /proc, other sessions' files, and write to the host.
    """

    def __init__(self, provider: str) -> None:
        ValueError.__init__(
            self,
            f"sandbox provider {provider!r} runs agent commands on the host; it is refused in "
            "production and staging. Configure local_docker or e2b, or set ALLOW_HOST_SANDBOX=1 "
            "if this deployment is single-tenant and trusted.",
        )
        self.provider = provider


def host_sandbox_allowed() -> bool:
    """Host-process sandboxes: fine in development, refused in production and staging
    unless an operator opts in (``ALLOW_HOST_SANDBOX``)."""
    env = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).strip().lower()
    opted_in = os.environ.get("ALLOW_HOST_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on")
    return opted_in or env not in ("production", "staging")


def assert_host_sandbox_allowed(provider_name: str) -> None:
    """Raise ``HostSandboxRefused`` for a host-process provider where it is not allowed."""
    name = provider_name.strip().lower()
    if name in _HOST_PROVIDERS and not host_sandbox_allowed():
        raise HostSandboxRefused(name)


def multi_tenant_strict_enabled() -> bool:
    """``MULTI_TENANT_STRICT``: tenants share this deployment, so sandboxes must isolate them."""
    return os.environ.get("MULTI_TENANT_STRICT", "false").strip().lower() in ("1", "true", "yes", "on")


def assert_multi_tenant_provider(
    provider_name: str,
    *,
    multi_tenant_strict: bool,
) -> None:
    """Assert provider is permitted under multi-tenant policy.

    In strict multi-tenant mode, only providers with full isolation are
    allowed. This prevents accidental data leakage between tenants.

    Args:
        provider_name: Name of the sandbox provider.
        multi_tenant_strict: Whether strict mode is enabled.

    Raises:
        MultiTenantViolation: If provider violates policy.

    Example:
        >>> assert_multi_tenant_provider("e2b", multi_tenant_strict=True)
        # OK
        >>> assert_multi_tenant_provider("local_docker", multi_tenant_strict=True)
        # Raises MultiTenantViolation
    """
    if not multi_tenant_strict:
        return

    name = provider_name.strip().lower()
    if name in _UNSAFE_PROVIDERS:
        raise MultiTenantViolation(name)


# =============================================================================
# Image Digest Validation
# =============================================================================

# SHA-256 hex digest pattern
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^sha256:([0-9a-f]{64})$",
    re.IGNORECASE,
)

# Placeholder markers (case-insensitive)
_PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = (
    "REPLACE",
    "PLACEHOLDER",
    "TODO",
    "FIXME",
    "CHANGE_ME",
    "INSERT_HERE",
)


class DigestStatus(StrEnum):
    """Result of image digest validation."""

    VALID = auto()
    EMPTY = auto()
    PLACEHOLDER = auto()
    FIXTURE = auto()
    MALFORMED = auto()


@dataclass(frozen=True, slots=True)
class SecurityCheckResult:
    """Result of a security validation check."""

    passed: bool
    status: DigestStatus
    message: str

    @classmethod
    def ok(cls) -> SecurityCheckResult:
        return cls(passed=True, status=DigestStatus.VALID, message="valid digest")

    @classmethod
    def fail(cls, status: DigestStatus, message: str) -> SecurityCheckResult:
        return cls(passed=False, status=status, message=message)


def is_placeholder_image_digest(digest: str | None) -> bool:
    """Check if image digest is a placeholder or fixture value.

    Detects:
        - Empty or None digests
        - Placeholder markers (REPLACE, PLACEHOLDER, TODO, etc.)
        - Fixture digests (all same character: sha256:aaaa...aaaa)

    Args:
        digest: Image digest string to validate.

    Returns:
        True if digest is a placeholder/fixture (should be rejected).

    Example:
        >>> is_placeholder_image_digest(None)
        True
        >>> is_placeholder_image_digest("sha256:PLACEHOLDER")
        True
        >>> is_placeholder_image_digest("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        True
        >>> is_placeholder_image_digest("sha256:abc123...")  # real digest
        False
    """
    if not digest:
        return True

    d = digest.strip()
    if not d:
        return True

    upper = d.upper()

    # Check for placeholder markers
    for marker in _PLACEHOLDER_MARKERS:
        if marker in upper:
            return True

    # Validate SHA-256 format and check for fixture patterns
    match = _SHA256_PATTERN.match(d)
    if not match:
        # Not a valid SHA-256 format, but not necessarily a placeholder
        # Return False here - let validate_image_digest catch format errors
        return False

    # Check for fixture patterns (all same character)
    # e.g., sha256:aaaa...aaaa or sha256:0000...0000
    return len(set(match.group(1).lower())) == 1


# =============================================================================
# Backward Compatibility
# =============================================================================

# Alias for backward compatibility with gate.py
assert_multi_tenant_sandbox_provider = assert_multi_tenant_provider
