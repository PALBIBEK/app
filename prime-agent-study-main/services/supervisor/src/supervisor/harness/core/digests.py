"""Content digests for harness manifests.

The hashing itself (canonical JSON, sha256) is the catalog's, so a harness
and a catalog artifact with the same content always have the same digest.
"""

from __future__ import annotations

import re
from typing import Any, Final

from supervisor.catalog.core.digests import canonical_json_bytes, content_digest

__all__ = [
    "DIGEST_PATTERN",
    "canonical_json_bytes",
    "content_digest",
    "validate_digest_format",
    "verify_digest",
]

# Regex pattern for valid sha256 digests: sha256:<64 hex chars>
DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
# An OCI repository: optional registry host[:port], then lowercase path components.
REPOSITORY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:[a-zA-Z0-9.-]+(?::[0-9]+)?/)?[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)


def verify_digest(obj: Any, expected: str) -> None:
    """Raise InvalidDigest if body does not match expected digest.

    Args:
        obj: JSON-serializable object
        expected: Expected digest string

    Raises:
        InvalidDigest: If computed digest does not match expected
    """
    from supervisor.harness.core.errors import InvalidDigest

    actual = content_digest(obj)
    if actual != expected.lower():
        raise InvalidDigest(expected, f"computed={actual}")


def validate_digest_format(digest: str) -> None:
    """Validate digest format is sha256:<64 hex>.

    Args:
        digest: Digest string to validate

    Raises:
        InvalidDigest: If format is invalid
    """
    from supervisor.harness.core.errors import InvalidDigest

    if not DIGEST_PATTERN.match(digest):
        if digest.startswith("sha256:"):
            raise InvalidDigest(digest, "must be sha256:<64 hex chars>")
        if ":" in digest:
            raise InvalidDigest(digest, "must use sha256 algorithm")
        raise InvalidDigest(digest, "missing sha256: prefix")


def assert_digest(value: str) -> None:
    """Assert that value is a sha256 digest, bare or pinning a repository (repo@sha256:...).

    This is used for validating image_digest fields in manifests.

    Args:
        value: Value to validate

    Raises:
        ManifestValidationError: If value is not a valid digest
    """
    from supervisor.harness.core.errors import ManifestValidationError

    if value.startswith("sha256:") and DIGEST_PATTERN.match(value):
        return
    # A digest-pinned reference (repository@sha256:...) is as immutable as a bare
    # digest, and a registry can pull it; a tag (repository:tag) is neither.
    repository, at, digest = value.rpartition("@")
    if at and REPOSITORY_PATTERN.match(repository) and DIGEST_PATTERN.match(digest):
        return
    if ":" in value and not value.startswith("sha256:"):
        raise ManifestValidationError(
            "image_digest",
            f"must be a sha256 digest, not a tag: {value!r}",
        )
    if not value.startswith("sha256:"):
        raise ManifestValidationError(
            "image_digest",
            f"must be a sha256 digest, got {value!r}",
        )
    # sha256: but wrong length
    raise ManifestValidationError(
        "image_digest",
        f"must be sha256:<64 hex>, got {value!r}",
    )
