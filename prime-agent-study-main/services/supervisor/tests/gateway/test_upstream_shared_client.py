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
