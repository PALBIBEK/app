"""Read supervisor-materialized .yal/plug_bindings.json.

Provides utilities for reading plug bindings and launch configuration
from the .yal/ directory.

Reference: Original plug_bindings.py with added path validation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from supervisor.harness.storage.validators import validate_session_dir

__all__ = [
    "read_launch_cli",
    "read_plug_bindings",
    "yal_dir",
]

logger = logging.getLogger(__name__)


def yal_dir(session_dir: Path | str) -> Path:
    """Get .yal directory path for session.

    Args:
        session_dir: Session directory path

    Returns:
        Path to .yal directory

    Raises:
        PathTraversalAttempt: If session_dir contains path traversal
    """
    validated = validate_session_dir(session_dir)
    return validated / ".yal"


def read_plug_bindings(session_dir: Path | str | None) -> dict[str, str]:
    """Read plug_bindings.json from session directory.

    Args:
        session_dir: Session directory path or None

    Returns:
        Dict of plug bindings (string key-value pairs)
    """
    if session_dir is None:
        return {}

    try:
        path = yal_dir(session_dir) / "plug_bindings.json"
    except Exception as exc:
        logger.warning(
            "Invalid session directory",
            extra={"session_dir": str(session_dir), "error": str(exc)},
        )
        return {}

    if not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning(
                "plug_bindings.json is not a dict",
                extra={"path": str(path)},
            )
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception as exc:
        logger.warning(
            "Failed to read plug_bindings.json",
            extra={"path": str(path), "error": str(exc)},
        )
        return {}


def read_launch_cli(session_dir: Path | str | None) -> str | None:
    """Read CLI path from launch.json.

    Args:
        session_dir: Session directory path or None

    Returns:
        CLI path or None if not configured
    """
    if session_dir is None:
        return None

    try:
        path = yal_dir(session_dir) / "launch.json"
    except Exception as exc:
        logger.warning(
            "Invalid session directory",
            extra={"session_dir": str(session_dir), "error": str(exc)},
        )
        return None

    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cli = data.get("cli") if isinstance(data, dict) else None
        return str(cli).strip() if cli else None
    except Exception as exc:
        logger.warning(
            "Failed to read launch.json",
            extra={"path": str(path), "error": str(exc)},
        )
        return None


def read_yal_file(session_dir: Path | str, filename: str) -> dict[str, Any] | None:
    """Read a JSON file from .yal directory.

    Args:
        session_dir: Session directory path
        filename: Name of file to read

    Returns:
        Parsed JSON dict or None if not found/invalid
    """
    try:
        path = yal_dir(session_dir) / filename
    except Exception:
        return None

    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None
