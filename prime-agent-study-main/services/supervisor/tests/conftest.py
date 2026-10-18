"""pytest hooks: --with-harness enables the harness parameter in the shared suite."""

from __future__ import annotations

import pytest

pytest_plugins = ["tests.support.session_env"]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--with-harness",
        action="store_true",
        default=False,
        help="also run shared AgentRuntime tests against the harness subprocess runtime",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "harness: exercises the harness subprocess runtime (fixture or live)"
    )


@pytest.fixture(autouse=True)
def _default_harness_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CLI", "mock-harness-agent")
