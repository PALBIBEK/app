"""Configuration from the environment."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def model() -> str:
    return os.environ.get("GITHI_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    return os.environ.get("GITHI_BASE_URL", DEFAULT_BASE_URL)


def skill_roots(workspace: Path) -> list[Path]:
    """Bundled skills first, then the workspace's own.

    Order is precedence -- `SkillLibrary.discover` keeps the first definition
    of a name -- so a file dropped in a workspace cannot silently replace a
    shipped playbook with one nobody reviewed.
    """
    return [Path(__file__).parent / "skills", workspace / "skills"]
