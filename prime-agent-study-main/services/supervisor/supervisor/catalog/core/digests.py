"""Content digests: how an artifact is addressed, and which references are exact.

A digest is ``sha256:`` + the lowercase hex sha256 of the artifact's canonical JSON
body (``body_for_digest()``). Sessions pin artifacts by digest, so a digest is a
proof of content: the catalog recomputes it on every offer and refuses one that
does not match (``verify_digest``), and refuses references that could move
(``reject_floating_ref``) or are not digests at all (``validate_digest_format``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

__all__ = [
    "canonical_json_bytes",
    "content_digest",
    "is_unset_digest",
    "reject_floating_ref",
    "validate_digest_format",
    "verify_digest",
]

_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")

# Authoring markers meaning "compute the digest on offer".
_UNSET_DIGEST_PREFIXES: Final[tuple[str, ...]] = ("sha256:PLACEHOLDER", "sha256:REPLACE")


def canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8 JSON with sorted keys and no insignificant whitespace."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def content_digest(obj: Any) -> str:
    """Return ``sha256:`` + lowercase hex of canonical UTF-8 JSON body."""
    digest = hashlib.sha256(canonical_json_bytes(obj)).hexdigest()
    return f"sha256:{digest}"


def is_unset_digest(digest: str | None) -> bool:
    """True when a digest is absent or an authoring placeholder to be computed."""
    return not digest or digest.startswith(_UNSET_DIGEST_PREFIXES)


def verify_digest(obj: Any, expected: str) -> str:
    """The digest of ``obj``; ``DigestMismatch`` if it is not ``expected``."""
    from supervisor.catalog.core.errors import DigestMismatch

    actual = content_digest(obj)
    if actual != expected:
        raise DigestMismatch(expected=expected, actual=actual)
    return actual


def validate_digest_format(digest: str) -> None:
    """``InvalidDigest`` unless ``digest`` is ``sha256:`` and 64 lowercase hex digits."""
    from supervisor.catalog.core.errors import InvalidDigest

    if not digest:
        raise InvalidDigest("", "empty digest")
    if not digest.startswith("sha256:"):
        raise InvalidDigest(digest, "must start with 'sha256:'")
    if not _DIGEST.fullmatch(digest):
        raise InvalidDigest(digest, "must be sha256: and 64 lowercase hex digits")


def reject_floating_ref(ref: str) -> None:
    """``FloatingRef`` for a reference that could move: empty, ``latest``, or a broken digest."""
    from supervisor.catalog.core.errors import FloatingRef

    s = ref.strip()
    if not s:
        raise FloatingRef("", "empty composition reference")
    lower = s.lower()
    if lower == "latest" or lower.endswith((":latest", "@latest")):
        raise FloatingRef(ref, "exact digest or id required")
    if s.startswith("sha256:") and not _DIGEST.fullmatch(s):
        raise FloatingRef(ref, "malformed digest format")
