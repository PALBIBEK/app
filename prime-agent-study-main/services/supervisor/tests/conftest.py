"""Shared pytest plugins for the supervisor suite."""

from __future__ import annotations

pytest_plugins = [
    "tests.support.session_env",
    "tests.support.journal_db",
    "tests.support.temporal_env",
    "tests.support.githi_env",
    "tests.support.notebooks",
    "tests.support.memory_pg",
    "tests.support.trace_api",
    "tests.support.browser",
]
