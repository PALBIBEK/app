"""Detection utilities — hash-based call fingerprinting.

Provides utilities for detecting repeated or similar tool calls:
- Canonicalize arguments for stable comparison
- Generate hashes for call fingerprinting
- Normalize strings for fuzzy matching

Uses xxhash for fast hashing (falls back to hashlib if unavailable).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "args_similar",
    "call_hash",
    "canonicalize_args",
    "normalize_string",
]


# Try to use xxhash for speed, fall back to hashlib
try:
    import xxhash

    def _fast_hash(data: bytes) -> str:
        """Fast hash using xxhash."""
        return xxhash.xxh64(data).hexdigest()[:16]

except ImportError:

    def _fast_hash(data: bytes) -> str:
        """Fallback hash using hashlib."""
        return hashlib.sha256(data).hexdigest()[:16]


def normalize_string(s: str) -> str:
    """Normalize string for comparison.

    - Collapses whitespace
    - Strips leading/trailing whitespace
    - Preserves case (important for code)

    Args:
        s: String to normalize

    Returns:
        Normalized string
    """
    return " ".join(s.split())


def canonicalize_args(args: dict[str, Any]) -> str:
    """Canonicalize arguments for stable hashing.

    Produces deterministic JSON representation:
    - Sorted keys
    - Normalized whitespace in strings
    - Compact separators

    Args:
        args: Tool arguments dict

    Returns:
        Canonical JSON string
    """

    def normalize_value(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: normalize_value(obj[k]) for k in sorted(obj.keys())}
        if isinstance(obj, list):
            return [normalize_value(x) for x in obj]
        if isinstance(obj, str):
            return normalize_string(obj)
        return obj

    normalized = normalize_value(args)
    return json.dumps(normalized, separators=(",", ":"), sort_keys=True)


def call_hash(tool_name: str, args: dict[str, Any]) -> str:
    """Generate hash fingerprint for tool call.

    Args:
        tool_name: Name of tool being called
        args: Tool arguments

    Returns:
        16-character hex hash
    """
    canonical = canonicalize_args(args)
    payload = f"{tool_name}|{canonical}"
    return _fast_hash(payload.encode("utf-8"))


def args_similar(
    args1: dict[str, Any],
    args2: dict[str, Any],
    *,
    threshold: float = 0.9,
) -> bool:
    """Check if two argument sets are similar.

    Uses simple string comparison of canonical forms.
    For more sophisticated similarity, consider:
    - Levenshtein distance
    - Jaccard similarity
    - Semantic embedding comparison

    Args:
        args1: First argument set
        args2: Second argument set
        threshold: Similarity threshold (0-1)

    Returns:
        True if arguments are similar
    """
    # For now, use exact hash match
    # Can be extended with fuzzy matching later
    return call_hash("_", args1) == call_hash("_", args2)


def extract_key_fields(args: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Extract specific fields for focused comparison.

    Useful when only certain fields matter for repetition detection.

    Args:
        args: Full argument dict
        keys: Keys to extract

    Returns:
        Subset of args with only specified keys
    """
    return {k: args[k] for k in keys if k in args}
