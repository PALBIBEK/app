"""Input validation helpers for security.

Provides path traversal detection, environment variable sanitization,
and other security-related validation utilities.

Reference: Security best practices for path and input handling.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from supervisor.harness.core.errors import (
    InvalidEnvironmentValue,
    PathTraversalAttempt,
)

__all__ = [
    "get_env_url",
    "sanitize_env_value",
    "validate_path_safe",
    "validate_session_dir",
]

# Pattern for unsafe control characters in environment values
_UNSAFE_ENV_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def validate_path_safe(path: str | Path, base: Path) -> Path:
    """Validate path does not escape base directory.

    Ensures the resolved path is within the base directory,
    preventing path traversal attacks.

    Args:
        path: Path to validate
        base: Base directory that must contain path

    Returns:
        Resolved absolute path

    Raises:
        PathTraversalAttempt: If path escapes base
    """
    resolved = Path(path).resolve()
    base_resolved = base.resolve()

    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise PathTraversalAttempt(str(path))

    return resolved


def validate_session_dir(session_dir: str | Path) -> Path:
    """Validate session directory path for safety.

    Checks for path traversal attempts using .. components.

    Args:
        session_dir: Session directory path to validate

    Returns:
        Path object (not resolved, for flexibility)

    Raises:
        PathTraversalAttempt: If path contains traversal attempts
    """
    path = Path(session_dir)

    # Check for path traversal attempts in POSIX style
    posix = PurePosixPath(str(session_dir))
    for part in posix.parts:
        if part == "..":
            raise PathTraversalAttempt(str(session_dir))

    # Also check Windows-style paths
    str_path = str(session_dir)
    if ".." in str_path.split("\\") or ".." in str_path.split("/"):
        raise PathTraversalAttempt(str(session_dir))

    return path


def sanitize_env_value(key: str, value: str) -> str:
    """Sanitize environment variable value.

    Validates that the value does not contain control characters
    or other potentially dangerous content.

    Args:
        key: Variable name (for error messages)
        value: Value to sanitize

    Returns:
        Sanitized value (stripped of leading/trailing whitespace)

    Raises:
        InvalidEnvironmentValue: If value contains unsafe characters
    """
    if _UNSAFE_ENV_CHARS.search(value):
        raise InvalidEnvironmentValue(key, "contains control characters")

    return value.strip()


def get_env_url(key: str, default: str) -> str:
    """Get URL from environment with validation.

    Retrieves an environment variable expected to contain a URL,
    validates it, and returns with trailing slash removed.

    Args:
        key: Environment variable name
        default: Default value if not set

    Returns:
        Validated URL without trailing slash

    Raises:
        InvalidEnvironmentValue: If value contains unsafe characters
    """
    raw = os.environ.get(key, "").strip() or default
    sanitized = sanitize_env_value(key, raw)
    return sanitized.rstrip("/")


def get_env_optional(key: str) -> str | None:
    """Get optional environment variable with validation.

    Args:
        key: Environment variable name

    Returns:
        Validated value or None if not set

    Raises:
        InvalidEnvironmentValue: If value contains unsafe characters
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        return None
    return sanitize_env_value(key, raw)


def get_env_required(key: str) -> str:
    """Get required environment variable with validation.

    Args:
        key: Environment variable name

    Returns:
        Validated value

    Raises:
        InvalidEnvironmentValue: If not set or contains unsafe characters
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        raise InvalidEnvironmentValue(key, "is required but not set")
    return sanitize_env_value(key, raw)
