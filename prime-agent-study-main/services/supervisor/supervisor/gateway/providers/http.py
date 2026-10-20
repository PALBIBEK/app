"""The HTTP leg every vendor provider shares: auth headers, retries, SSE relay."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from supervisor.gateway.providers.base import SSE, Body, ProviderUnconfigured, UpstreamError

_TRANSIENT_STATUS = frozenset({408, 409, 429}) | frozenset(range(500, 600))
_MAX_BACKOFF_S = 5.0

type AuthHeaders = Callable[[str], Mapping[str, str]]


def bearer(key: str) -> Mapping[str, str]:
    return {"Authorization": f"Bearer {key}"}


@dataclass(slots=True)
class HttpUpstream:
    """POSTs a JSON body to ``base_url + path`` and returns JSON or the SSE byte stream.

    Transient failures (HTTP 408/409/429/5xx, connection errors, timeouts) are
    retried with capped exponential backoff, honouring ``Retry-After``. A stream is
    retried only before its first byte is relayed.
    """

    vendor: str
    api_key: str
    base_url: str
    key_env_name: str
    auth: AuthHeaders = bearer
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    transport: httpx.BaseTransport | None = None
    retries: int = 1
    timeout_s: float = 600.0
    backoff_s: float = 0.2

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def credential_ok(self) -> bool:
        return bool(self.api_key.strip())

    def post(self, path: str, body: Body, *, retries: int | None = None) -> Body | SSE:
        if not self.credential_ok():
            raise ProviderUnconfigured(f"{self.key_env_name} is not configured on the gateway")
        attempts = 1 + max(0, self.retries if retries is None else retries)
        stream = body.get("stream") is True
        for attempt in range(attempts):
            client = httpx.Client(transport=self.transport, timeout=self.timeout_s)
            try:
                response = client.send(self._request(client, path, body), stream=stream)
            except httpx.TransportError as exc:
                client.close()
                if attempt + 1 < attempts:
                    self._sleep(attempt, None)
                    continue
                raise UpstreamError(
                    f"{self.vendor} upstream unreachable: {type(exc).__name__}"
                ) from exc
            if response.is_success:
                if stream:
                    return _relay(response, client)
                try:
                    return _json(response, self.vendor)
                finally:
                    response.close()
                    client.close()
            status = response.status_code
            detail = _error_detail(response, stream=stream)
            response.close()
            client.close()
            if status in _TRANSIENT_STATUS and attempt + 1 < attempts:
                self._sleep(attempt, response.headers.get("retry-after"))
                continue
            raise UpstreamError(
                f"{self.vendor} upstream returned HTTP {status}: {detail}", status=status
            )
        raise AssertionError("unreachable")

    def _request(self, client: httpx.Client, path: str, body: Body) -> httpx.Request:
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
            **self.auth(self.api_key),
        }
        if body.get("stream") is True:
            headers["Accept"] = "text/event-stream"
        return client.build_request("POST", f"{self.base_url}{path}", json=body, headers=headers)

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff_s * (2**attempt)
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
        time.sleep(min(max(0.0, delay), _MAX_BACKOFF_S))


def _json(response: httpx.Response, vendor: str) -> Body:
    try:
        parsed: Any = response.json()
    except ValueError as exc:
        raise UpstreamError(f"{vendor} upstream returned a non-JSON body") from exc
    if not isinstance(parsed, dict):
        raise UpstreamError(f"{vendor} upstream returned a non-object body")
    return parsed


def _relay(response: httpx.Response, client: httpx.Client) -> Iterator[bytes]:
    try:
        yield from response.iter_bytes()
    finally:
        response.close()
        client.close()


def _error_detail(response: httpx.Response, *, stream: bool) -> str:
    """The upstream's own error message, bounded; never echoes request content back."""
    try:
        raw = response.read() if stream else response.content
    except httpx.HTTPError:
        return "no detail"
    try:
        parsed = httpx.Response(200, content=raw).json()
    except ValueError:
        return raw[:200].decode("utf-8", errors="replace") or "no detail"
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error
        return str(message)[:300]
    return str(error or parsed)[:300]
