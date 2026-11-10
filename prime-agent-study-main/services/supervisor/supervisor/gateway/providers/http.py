"""The HTTP leg every vendor provider shares: auth headers, retries, SSE relay."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from supervisor.gateway.providers.base import (
    SSE,
    Body,
    ProviderUnconfigured,
    UpstreamError,
    UpstreamUnreachable,
)

_CLOSED_CLIENT = "client has been closed"
"""httpx's words when a client closed by another thread is used (``HttpUpstream.close``)."""
_TRANSIENT_STATUS = frozenset({408, 409, 429}) | frozenset(range(500, 600))
_MAX_BACKOFF_S = 5.0
_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=64, keepalive_expiry=5.0)
"""For the default transport (a transport given to an upstream, a test's, has its own).
Connections are bounded by the gateway's request threads (``supervisor.main``), not
here; up to 64 stay open between calls for the next one to reuse, each for 5 s idle: well
inside any vendor's or balancer's idle timeout, so a pooled connection is not one the far
end has closed (and httpx checks one before reusing it)."""

type AuthHeaders = Callable[[str], Mapping[str, str]]
type ApiKey = str | Callable[[], str]
type BaseUrl = str | Callable[[], str]
"""A fixed base URL, or one read on every call (a local server's, set from the product)."""
"""A fixed key, or one read on every call (a key set through the gateway's API)."""


def bearer(key: str) -> Mapping[str, str]:
    return {"Authorization": f"Bearer {key}"}


@dataclass(slots=True)
class HttpUpstream:
    """POSTs a JSON body to ``base_url + path`` and returns JSON or the SSE byte stream.

    Transient failures (HTTP 408/409/429/5xx, connection errors, timeouts) are
    retried with capped exponential backoff, honouring ``Retry-After``. A stream is
    retried only before its first byte is relayed.

    Every call goes through one client, made on the first: a client per call built a TLS
    context (it parses the CA bundle) and a TLS handshake with the vendor each time, and
    reused no connection, on the busiest path there is. ``httpx.Client`` is safe across
    threads; each response is closed when read, the client never.
    """

    vendor: str
    api_key: ApiKey
    base_url: BaseUrl
    key_env_name: str
    base_url_env_name: str = ""
    key_required: bool = True
    """False for a server that takes no key (a local one): requests then carry no auth header."""
    auth: AuthHeaders = bearer
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    transport: httpx.BaseTransport | None = None
    retries: int = 1
    timeout_s: float = 600.0
    backoff_s: float = 0.2
    _client: httpx.Client | None = field(default=None, init=False, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def client(self) -> httpx.Client:
        """The upstream's one client, made on first use from ``transport`` and
        ``timeout_s`` (read then, once: an upstream's connection settings are fixed)."""
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(transport=self.transport, timeout=self.timeout_s, limits=_LIMITS)
            return self._client

    def close(self) -> None:
        """Close the client and its pooled connections; the next call makes a new one."""
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def url(self) -> str:
        """The base URL now, without a trailing slash."""
        return (self.base_url() if callable(self.base_url) else self.base_url).strip().rstrip("/")

    def key(self) -> str:
        return (self.api_key() if callable(self.api_key) else self.api_key).strip()

    def missing(self) -> str | None:
        """The setting the gateway lacks to call this upstream, or None when it has them."""
        if not self.url():
            return self.base_url_env_name or "the upstream's base URL"
        if self.key_required and not self.key():
            return self.key_env_name
        return None

    def credential_ok(self) -> bool:
        return self.missing() is None

    def post(self, path: str, body: Body, *, retries: int | None = None) -> Body | SSE:
        if (missing := self.missing()) is not None:
            raise ProviderUnconfigured(f"{missing} is not configured on the gateway", vendor=self.vendor)
        attempts = 1 + max(0, self.retries if retries is None else retries)
        stream = body.get("stream") is True
        for attempt in range(attempts):
            client = self.client()  # each attempt: one closed meanwhile (``close``) is made anew
            try:
                try:
                    response = client.send(self._request(client, path, body), stream=stream)
                except RuntimeError as exc:
                    if not (client.is_closed and _CLOSED_CLIENT in str(exc)):
                        raise  # any other RuntimeError is a fault, never retried as a dropped connection
                    # Closed by another thread under this call: retried, like a dropped connection.
                    raise httpx.TransportError("the upstream's client was closed during the call") from exc
            except httpx.TransportError as exc:
                if attempt + 1 < attempts:
                    self._sleep(attempt, None)
                    continue
                raise UpstreamUnreachable(
                    f"{self.vendor} upstream unreachable: {type(exc).__name__}", vendor=self.vendor
                ) from exc
            if response.is_success:
                if stream:
                    return _relay(response)
                try:
                    return _json(response, self.vendor)
                finally:
                    response.close()
            status = response.status_code
            detail = _error_detail(response, stream=stream)
            response.close()
            if status in _TRANSIENT_STATUS and attempt + 1 < attempts:
                self._sleep(attempt, response.headers.get("retry-after"))
                continue
            raise UpstreamError(
                f"{self.vendor} upstream returned HTTP {status}: {detail}", status=status
            )
        raise AssertionError("unreachable")

    def _request(self, client: httpx.Client, path: str, body: Body) -> httpx.Request:
        key = self.key()
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
            **(self.auth(key) if key else {}),
        }
        if body.get("stream") is True:
            headers["Accept"] = "text/event-stream"
        return client.build_request("POST", f"{self.url()}{path}", json=body, headers=headers)

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


class _Relay(Iterator[bytes]):
    """An upstream stream's bytes, its response closed (its connection back in the shared
    pool) when the stream ends, fails, is closed, or is dropped unread.

    Not a generator: one that is never started never runs its ``finally``, and a caller
    that goes away before the first read (a client that disconnects, a wrapper that fails
    first) would leave the response holding its pooled connection for good."""

    _response: httpx.Response | None = None  # set first: a failed __init__ leaves None for __del__
    _closed: bool = False

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._bytes = response.iter_bytes()

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration  # closed (by its consumer, or at its end): it has ended
        try:
            return next(self._bytes)
        except BaseException:  # the end of the stream, or its failure
            self.close()
            raise

    def close(self) -> None:
        self._closed = True
        if self._response is not None:
            self._response.close()  # idempotent

    def __del__(self) -> None:
        self.close()


def _relay(response: httpx.Response) -> _Relay:
    return _Relay(response)


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
