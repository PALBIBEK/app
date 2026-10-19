"""Content digests for harness manifests.

Provides sha256 digests of canonical JSON for content-addressable
artifact identification.

Reference: catalog/core/digests.py pattern.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

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


def canonical_json_bytes(obj: Any) -> bytes:
    """Return UTF-8 JSON with sorted keys and no insignificant whitespace.

    This produces a deterministic byte sequence for any JSON-serializable
    object, suitable for computing content digests.

    Args:
        obj: JSON-serializable object

    Returns:
        Canonical UTF-8 encoded JSON bytes
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def content_digest(obj: Any) -> str:
    """Return sha256:<hex> digest of canonical UTF-8 JSON body.

    Args:
        obj: JSON-serializable object

    Returns:
        Digest string in format sha256:<64 hex chars>
    """
    digest = hashlib.sha256(canonical_json_bytes(obj)).hexdigest().lower()
    return f"sha256:{digest}"


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
