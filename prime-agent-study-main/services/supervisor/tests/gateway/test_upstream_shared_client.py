"""The gateway's upstreams share one client each (G1 of ``docs/memory-gaps.md``), so every
stream must give its connection back: when it is read to its end, when its caller closes
it partway, and when it is dropped before its first read."""

from __future__ import annotations

import gc
from collections.abc import Iterator

import httpx

from supervisor.gateway.providers.http import HttpUpstream


class Tracked(httpx.SyncByteStream):
    """An upstream body that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield b"data: 1\n\n"
        yield b"data: 2\n\n"

    def close(self) -> None:
        self.closed = True


def upstream() -> tuple[HttpUpstream, list[Tracked]]:
    bodies: list[Tracked] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(Tracked())
        return httpx.Response(200, stream=bodies[-1], headers={"content-type": "text/event-stream"})

    made = HttpUpstream(vendor="v", api_key="k", base_url="https://v.example", key_env_name="V_KEY",
                        transport=httpx.MockTransport(handle))
    return made, bodies


def stream(up: HttpUpstream) -> Iterator[bytes]:
    result = up.post("/chat", {"stream": True})
    assert not isinstance(result, dict)
    return result


def test_every_call_goes_through_one_client() -> None:
    up, _ = upstream()

    assert up.client() is up.client()
    for _ in range(3):
        b"".join(stream(up))
    assert not up.client().is_closed, "the shared client outlives its calls"


def test_a_stream_read_to_its_end_gives_its_connection_back() -> None:
    up, bodies = upstream()

    assert b"".join(stream(up)) == b"data: 1\n\ndata: 2\n\n"
    assert bodies[0].closed


def test_a_stream_closed_partway_gives_its_connection_back() -> None:
    up, bodies = upstream()

    relay = stream(up)
    next(relay)
    relay.close()  # type: ignore[attr-defined]

    assert bodies[0].closed


def test_a_stream_dropped_before_its_first_read_gives_its_connection_back() -> None:
    """A caller that went away before reading (a client that disconnected first) must not
    leave a pooled connection held for good."""
    up, bodies = upstream()

    relay = stream(up)
    del relay
    gc.collect()

    assert bodies[0].closed


def test_a_closed_stream_ends_cleanly_and_a_closed_upstream_makes_a_new_client() -> None:
    up, _ = upstream()
    relay = stream(up)
    relay.close()  # type: ignore[attr-defined]

    assert list(relay) == [], "a stream closed by its consumer has ended, not failed"

    first = up.client()
    up.close()
    assert first.is_closed and up.client() is not first


def test_a_client_closed_under_a_call_is_retried_on_a_new_one() -> None:
    """``close`` on another thread (a reload, a teardown) while a call runs: the call gets
    a new client on its retry, not a raw error."""
    made: HttpUpstream

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    made = HttpUpstream(vendor="v", api_key="k", base_url="https://v.example", key_env_name="V_KEY",
                        transport=httpx.MockTransport(handle), backoff_s=0.0)
    made.client().close()  # closed under it, its cached client left in place

    assert made.post("/chat", {}) == {"ok": True}


def test_a_cookie_one_caller_was_set_never_goes_out_on_anothers_call() -> None:
    """One client serves every tenant's calls to a vendor: a cookie a response set (a
    balancer's, a vendor's bot management) is not kept for the next caller."""
    sent: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        return httpx.Response(200, json={}, headers={"set-cookie": "__cf_bm=tenant-a; Path=/"})

    up = HttpUpstream(vendor="v", api_key="k", base_url="https://v.example", key_env_name="V_KEY",
                      transport=httpx.MockTransport(handle))
    up.post("/chat", {})
    up.post("/chat", {})

    assert sent == [None, None]
