"""In-memory repository implementations for development and testing.

Design principles:
- Full Protocol compliance
- Thread-safe operations (asyncio-safe)
- No external dependencies
- Fast iteration for tests
- Complete feature parity with Postgres

Reference: Refactored from store.py with Protocol compliance.
"""

from __future__ import annotations

from supervisor.catalog.core.errors import (
    FloatingRef,
    InvalidDigest,
)

__all__ = [
    "reject_floating_ref",
    "validate_digest_format",
]


# =============================================================================
# Validation Helpers
# =============================================================================

def reject_floating_ref(ref: str) -> None:
    """Refuse mutable tags like 'latest'.

    Args:
        ref: Reference string to validate

    Raises:
        FloatingRef: If reference is floating/mutable
    """
    s = ref.strip()
    if not s:
        raise FloatingRef("", "empty composition reference")
    lower = s.lower()
    if lower == "latest" or lower.endswith((":latest", "@latest")):
        raise FloatingRef(ref, "exact digest or id required")
    if s.startswith("sha256:") and len(s) != len("sha256:") + 64:
        raise FloatingRef(ref, "malformed digest format")


def validate_digest_format(digest: str) -> None:
    """Validate digest format.

    Args:
        digest: Digest string to validate

    Raises:
        InvalidDigest: If digest format is invalid
    """
    if not digest:
        raise InvalidDigest("", "empty digest")
    if not digest.startswith("sha256:"):
        raise InvalidDigest(digest, "must start with 'sha256:'")
    hex_part = digest[7:]
    if len(hex_part) != 64:
        raise InvalidDigest(digest, f"hex part must be 64 chars, got {len(hex_part)}")
    if not all(c in "0123456789abcdef" for c in hex_part):
        raise InvalidDigest(digest, "hex part contains invalid characters")


# =============================================================================
# In-Memory Artifact Store
# =============================================================================

# =============================================================================
# In-Memory Usage Store
# =============================================================================

