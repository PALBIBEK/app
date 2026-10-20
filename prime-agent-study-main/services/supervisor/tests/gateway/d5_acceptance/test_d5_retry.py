"""D5 §6 A / T11 — retry on transient upstream 429."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app

from .helpers import SERVICE_TOKEN, configure_app


def test_t11_transient_429_retries_then_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T11: upstream 429 then 200 → gateway retries and returns success (or stable error)."""
    configure_app(monkeypatch, openai_key="sk-test-openai")
    calls = {"n": 0}

    success = {
        "id": "chatcmpl-retry",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "after-retry"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost_total": 0.0001,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=success)

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with (
        patch("supervisor.gateway.providers.openai_compat.httpx.Client", _PatchedClient),
        TestClient(app) as client,
    ):
        configure_app(monkeypatch, openai_key="sk-test-openai")
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "model": "openai-demo",
                "messages": [{"role": "user", "content": "retry me"}],
            },
        )

    # Preferred: retry then 200. Acceptable interim: stable typed error (not raw crash).
    if r.status_code == 200:
        assert calls["n"] >= 2, "success without retry — expected at least one 429 then retry"
        assert "after-retry" in json.dumps(r.json())
    else:
        assert r.status_code in (429, 502, 503)
        detail = r.json().get("detail") or {}
        err = detail.get("error") if isinstance(detail, dict) else {}
        assert isinstance(err, dict) and err.get("code"), r.text
        # Still require that retry was attempted when route meta promises retries,
        # or that route catalog documents retries — D5 requires retry tested.
        assert calls["n"] >= 2, (
            f"transient 429 must be retried (attempts={calls['n']}); "
            "configure route retries and retry loop"
        )
