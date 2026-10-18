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

import re
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final

__all__ = [
    "assert_multi_tenant_provider",
    "is_placeholder_image_digest",
    "validate_image_digest",
    "ImageDigestError",
    "MultiTenantViolation",
    "SecurityCheckResult",
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

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"provider {provider!r} is not permitted in MULTI_TENANT_STRICT mode; "
            f"use 'e2b' for isolation or 'none' for no-sandbox harnesses"
        )
        self.provider = provider


class ImageDigestError(ValueError):
    """Raised when image digest fails validation.

    Common causes:
        - Placeholder strings (sha256:PLACEHOLDER, REPLACE_ME)
        - Fixture digests (sha256:aaaa...aaaa, sha256:0000...0000)
        - Missing or empty digests
    """

    def __init__(self, digest: str | None, reason: str) -> None:
        super().__init__(
            f"invalid image digest {digest!r}: {reason}; "
            "build the harness image and pin the real digest (D14)"
        )
        self.digest = digest
        self.reason = reason


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
    hex_part = match.group(1).lower()
    if len(set(hex_part)) == 1:
        # e.g., sha256:aaaa...aaaa or sha256:0000...0000
        return True

    return False


def validate_image_digest(digest: str | None) -> SecurityCheckResult:
    """Validate image digest for security and correctness.

    Performs comprehensive validation:
        1. Non-empty check
        2. Placeholder detection
        3. Format validation (sha256:hex64)
        4. Fixture pattern detection

    Args:
        digest: Image digest to validate.

    Returns:
        SecurityCheckResult with validation outcome.

    Example:
        >>> result = validate_image_digest("sha256:abc123...")
        >>> result.passed
        True
        >>> result = validate_image_digest("sha256:PLACEHOLDER")
        >>> result.passed
        False
        >>> result.status
        DigestStatus.PLACEHOLDER
    """
    if not digest:
        return SecurityCheckResult.fail(
            DigestStatus.EMPTY,
            "image digest is required"
        )

    d = digest.strip()
    if not d:
        return SecurityCheckResult.fail(
            DigestStatus.EMPTY,
            "image digest cannot be empty"
        )

    upper = d.upper()

    # Check for placeholder markers
    for marker in _PLACEHOLDER_MARKERS:
        if marker in upper:
            return SecurityCheckResult.fail(
                DigestStatus.PLACEHOLDER,
                f"contains placeholder marker '{marker}'"
            )

    # Validate format
    match = _SHA256_PATTERN.match(d)
    if not match:
        return SecurityCheckResult.fail(
            DigestStatus.MALFORMED,
            "must match format sha256:<64 hex chars>"
        )

    # Check for fixture patterns
    hex_part = match.group(1).lower()
    if len(set(hex_part)) == 1:
        return SecurityCheckResult.fail(
            DigestStatus.FIXTURE,
            "fixture digest (all identical characters)"
        )

    # Check for other suspicious patterns
    if hex_part == "0" * 64 or hex_part == "f" * 64:
        return SecurityCheckResult.fail(
            DigestStatus.FIXTURE,
            "likely test fixture (all zeros or all f's)"
        )

    return SecurityCheckResult.ok()


def assert_valid_image_digest(digest: str | None) -> str:
    """Assert image digest is valid, raising on failure.

    Convenience wrapper around validate_image_digest that raises.

    Args:
        digest: Image digest to validate.

    Returns:
        The validated digest string (stripped).

    Raises:
        ImageDigestError: If validation fails.

    Example:
        >>> digest = assert_valid_image_digest("sha256:abc...")
        >>> assert_valid_image_digest(None)
        # Raises ImageDigestError
    """
    result = validate_image_digest(digest)
    if not result.passed:
        raise ImageDigestError(digest, result.message)

    return digest.strip() if digest else ""


# =============================================================================
# Backward Compatibility
# =============================================================================

# Alias for backward compatibility with gate.py
assert_multi_tenant_sandbox_provider = assert_multi_tenant_provider
