"""Shared pytest plugins for the supervisor suite."""

from __future__ import annotations

pytest_plugins = [
    "tests.support.session_env",
    "tests.support.temporal_env",
    "tests.support.githi_env",
    "tests.support.notebooks",
]
