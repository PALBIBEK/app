"""The real model-gateway service, served over HTTP for end-to-end tests.

The gateway is a sibling package; tests import it from its source tree and run
it under uvicorn on a free local port, so the supervisor talks to it exactly as
it would in a deployment.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

SERVICE_TOKEN = "gateway-service-token"
KEY_HEADER = "x-litellm-api-key"


def echo_group(*providers: str, **extra: Any) -> dict[str, Any]:
    """An echo route advertised as a model group of ``providers``."""
    return {
        "provider": "echo",
        "upstream_model": "echo",
        "providers": list(providers) or None,
        "context_window": 200_000,
        "max_output_tokens": 8_000,
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 2e-6,
        **extra,
    }


DEFAULT_GROUPS: Mapping[str, dict[str, Any]] = {
    "claude": echo_group("anthropic", supports_reasoning=True, supports_adaptive_thinking=True),
    "gpt": echo_group(
        "openai", supports_reasoning=True, supported_openai_params=["reasoning_effort"]
    ),
    "mix": echo_group("vertex_ai", "bedrock"),
    "gpt-6-astra": echo_group("openai", supports_reasoning=True),
}


@dataclass(frozen=True, slots=True)
class LiveGateway:
    url: str
    """Base URL, without ``/v1``."""
    token: str = SERVICE_TOKEN
    key_header: str = KEY_HEADER

    def env(self, **aliases: str) -> dict[str, str]:
        """The supervisor's ``MODEL_GATEWAY_*`` environment for this gateway."""
        return {
            "MODEL_GATEWAY_URL": f"{self.url}/v1",
            "MODEL_GATEWAY_API_KEY": self.token,
            "MODEL_GATEWAY_API_KEY_HEADER": self.key_header,
            "MODEL_GATEWAY_MODELS": ",".join(f"{k}={v}" for k, v in aliases.items()),
        }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serve_gateway(
    monkeypatch: pytest.MonkeyPatch, groups: Mapping[str, dict[str, Any]] = DEFAULT_GROUPS
) -> Iterator[LiveGateway]:
    import uvicorn

    from supervisor.gateway.app import app

    for name in (
        "AUTH_REQUIRED",
        "ENV",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY_HEADER", KEY_HEADER)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(dict(groups)))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("model gateway did not start")
        time.sleep(0.02)
    try:
        yield LiveGateway(f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
