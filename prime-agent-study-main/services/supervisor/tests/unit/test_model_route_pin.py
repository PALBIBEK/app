"""A composition's model pin changes only by an explicit deployment setting, and visibly.

Found by running the stack: with a provider key in the supervisor's environment,
every composition pinned to ``echo`` silently ran on Claude Sonnet.
"""

from __future__ import annotations

import logging

import pytest

from supervisor.catalog.providers.model_route import apply_runtime_model_route

SIGNALS = ("YAL_MODEL_ROUTE", "MODEL_GATEWAY_DEFAULT_ROUTE", "GATEWAY_VENDOR_READY")


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*SIGNALS, "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_a_provider_key_in_the_environment_does_not_change_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert apply_runtime_model_route("echo") == "echo"
    assert apply_runtime_model_route("or-free") == "or-free"


@pytest.mark.parametrize(
    ("signal", "value", "expected"),
    [("YAL_MODEL_ROUTE", "githi", "githi"), ("MODEL_GATEWAY_DEFAULT_ROUTE", "githi", "githi"),
     ("GATEWAY_VENDOR_READY", "1", "githi")],
)
def test_explicit_signals_still_upgrade_the_echo_placeholder_and_say_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, signal: str, value: str, expected: str
) -> None:
    monkeypatch.setenv(signal, value)
    with caplog.at_level(logging.WARNING):
        assert apply_runtime_model_route("echo") == expected
    assert "overridden" in caplog.text


def test_a_real_pin_is_kept_unless_ops_force_a_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_DEFAULT_ROUTE", "githi")
    assert apply_runtime_model_route("or-free") == "or-free"
    monkeypatch.setenv("YAL_MODEL_ROUTE", "githi")
    assert apply_runtime_model_route("or-free") == "githi"
