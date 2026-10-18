"""Content digests for composition catalog artifacts (sha256 of canonical JSON)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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


def verify_digest(obj: Any, expected: str) -> None:
    """Raise DigestMismatch if body does not match expected digest string."""
    from supervisor.catalog.core.errors import DigestMismatch

    actual = content_digest(obj)
    if actual != expected:
        raise DigestMismatch(expected=expected, actual=actual)
