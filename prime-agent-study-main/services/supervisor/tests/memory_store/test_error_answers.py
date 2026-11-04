"""An unexpected error is a 500 that carries a reference, never the database's text; the
supervisor's client tells a request the service rejected from a service that failed."""

from __future__ import annotations

import logging

import httpx
import pytest

from supervisor.memory.client import MemoryRequestInvalid, MemoryUnavailable, _error
from supervisor.memory_service.app import _map_error


def test_an_internal_error_hides_its_text_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    secret = "duplicate key value violates unique constraint; [SQL: INSERT INTO memory_session ...]"
    with caplog.at_level(logging.ERROR, logger="supervisor.memory_service.app"):
        answer = _map_error(RuntimeError(secret))
    assert answer.status_code == 500
    assert isinstance(answer.detail, dict)
    assert answer.detail["code"] == "INTERNAL"
    assert "SQL" not in answer.detail["message"] and "ref " in answer.detail["message"]
    ref = answer.detail["message"].rsplit("ref ", 1)[1].rstrip(")")
    assert any(ref in r.getMessage() and r.exc_info for r in caplog.records)


def _response(status: int, detail: object) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail}, request=httpx.Request("POST", "http://m/v1/x"))


@pytest.mark.parametrize("status", [400, 409, 413, 422])
def test_a_rejected_request_is_not_retryable(status: int) -> None:
    err = _error(_response(status, [{"msg": "field required"}]))
    assert isinstance(err, MemoryRequestInvalid) and not err.retryable


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_a_failing_or_busy_service_is_unavailable_and_retryable(status: int) -> None:
    err = _error(_response(status, "busy"))
    assert isinstance(err, MemoryUnavailable) and err.retryable
