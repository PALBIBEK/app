"""D5 E1 — runtime model_route upgrade without catalog digest churn."""

from __future__ import annotations

import pytest

from supervisor.catalog.model_route import apply_runtime_model_route


def test_echo_stays_echo_without_vendor_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YAL_MODEL_ROUTE", raising=False)
    monkeypatch.delenv("GATEWAY_VENDOR_READY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert apply_runtime_model_route("echo") == "echo"
    assert apply_runtime_model_route(None) is None


def test_yal_model_route_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YAL_MODEL_ROUTE", "githi")
    assert apply_runtime_model_route("echo") == "githi"


def test_echo_upgrades_when_openrouter_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YAL_MODEL_ROUTE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert apply_runtime_model_route("echo") == "githi"


def test_echo_upgrades_when_gateway_vendor_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YAL_MODEL_ROUTE", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GATEWAY_VENDOR_READY", "1")
    assert apply_runtime_model_route("echo") == "githi"


def test_echo_upgrades_when_default_route_is_githi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YAL_MODEL_ROUTE", raising=False)
    monkeypatch.delenv("GATEWAY_VENDOR_READY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_DEFAULT_ROUTE", "githi")
    assert apply_runtime_model_route("echo") == "githi"


def test_non_echo_pin_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YAL_MODEL_ROUTE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert apply_runtime_model_route("custom-route") == "custom-route"
