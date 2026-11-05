"""The models a local server runs, offered as routes without naming each one.

A local OpenAI-compatible server lists what it runs at ``GET /models`` (Ollama: "``/v1/models``
and ``/v1/models/{model}``"; vLLM and LM Studio alike). Each listed model becomes the route
``local/<model id>``, at no cost; a route configured in ``MODEL_GATEWAY_ROUTES_JSON`` for the same
id wins. The listing is cached as QM caches its model catalog (``src/model/model-catalog.ts``):
five minutes after a success, thirty seconds after a failure, one read at a time, at most 2 MiB
and 1000 models.

How much a local model holds is the server's setting, not the model's: Ollama runs every model
at its own context length (``OLLAMA_CONTEXT_LENGTH``; 4096 tokens on a machine without a large
GPU) and silently drops the start of a longer prompt. So the route's context window is the one
the server reports (vLLM's ``ModelCard.max_model_len``,
``vllm/entrypoints/serve/engine/protocol.py``), else ``LOCAL_MODEL_CONTEXT_WINDOW``, which must
match the server's. A model may answer with at most a quarter of it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

import httpx

from supervisor.gateway.routes import Route

__all__ = ["LOCAL_PREFIX", "LocalModels"]

logger = logging.getLogger(__name__)

LOCAL_PREFIX: Final = "local/"
TTL_S: Final = 5 * 60
FAILURE_TTL_S: Final = 30
TIMEOUT_S: Final = 3.0
MAX_BYTES: Final = 2 * 1024 * 1024
MAX_MODELS: Final = 1000
OUTPUT_SHARE: Final = 4
"""A local model's answer may take a quarter of its context window."""

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,184}$")
"""A listed id that, prefixed with ``local/``, is a valid route id."""


def local_route(upstream: str, context_window: int) -> Route:
    return Route(
        provider="local",
        upstream_model=upstream,
        providers=("local",),
        context_window=context_window,
        max_output_tokens=max(1, context_window // OUTPUT_SHARE),
        input_cost_per_token=0.0,
        output_cost_per_token=0.0,
        supported_openai_params=("max_tokens", "tools"),
    )


class LocalModels:
    """The routes of the models the local server lists, re-read when stale."""

    def __init__(
        self,
        base_url: Callable[[], str],
        api_key: Callable[[], str],
        *,
        context_window: int,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._context_window = context_window
        self._transport = transport
        self._now = now
        self._lock = threading.Lock()
        self._routes: dict[str, Route] = {}
        self._expires_at = 0.0
        self._read_from = ""
        self._error: str | None = None

    def routes(self) -> Mapping[str, Route]:
        """``local/<id>`` → route for each model the server lists (empty while it is down)."""
        base_url = self._base_url().strip().rstrip("/")
        if not base_url:
            return {}
        with self._lock:
            if self._now() >= self._expires_at or base_url != self._read_from:
                try:
                    self._routes, self._error = self._read(base_url), None
                    self._expires_at = self._now() + TTL_S
                except Exception as exc:  # noqa: BLE001 — any failure: no local models for a while
                    logger.warning("the local model server's list could not be read: %s", exc)
                    self._routes, self._error = {}, _said(exc)
                    self._expires_at = self._now() + FAILURE_TTL_S
                self._read_from = base_url
            return self._routes

    def expire(self) -> None:
        """Read the list again at the next ask (the server's address or key changed)."""
        with self._lock:
            self._expires_at = 0.0

    def error(self) -> str | None:
        """Why the last read of the server's list failed, or None (it was read, or never asked)."""
        return self._error

    def _read(self, base_url: str) -> dict[str, Route]:
        key = self._api_key().strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        with (
            httpx.Client(
                transport=self._transport, timeout=TIMEOUT_S, follow_redirects=False
            ) as client,
            client.stream("GET", f"{base_url}/models", headers=headers) as response,
        ):
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ValueError("the model list is too large")
        listed: Any = json.loads(bytes(body))
        data = listed.get("data") if isinstance(listed, dict) else None
        if not isinstance(data, list) or len(data) > MAX_MODELS:
            raise ValueError("not an OpenAI model list")
        routes: dict[str, Route] = {}
        for model in data:
            model_id = model.get("id") if isinstance(model, dict) else None
            if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
                continue
            reported = model.get("max_model_len")
            window = (
                reported if isinstance(reported, int) and reported > 1 else self._context_window
            )
            routes[LOCAL_PREFIX + model_id] = local_route(model_id, window)
        return routes


def _said(exc: Exception) -> str:
    """A read's failure as a person reads it: refused, unreachable, or not a model list."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"the server answered HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "the server did not answer in time"
    if isinstance(exc, httpx.HTTPError):
        return "the server could not be reached"
    return str(exc)[:200] or type(exc).__name__
