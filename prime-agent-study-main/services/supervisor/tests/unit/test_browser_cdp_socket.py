"""The runtime's WebSocket client (``supervisor.hands.browser.cdp_socket``) against a real
WebSocket server: what a CDP connection needs of RFC 6455."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from supervisor.hands.browser import cdp_socket

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def server() -> AsyncIterator[str]:
    """Echoes each message; ``big`` answers a 200 KB one, ``parts`` a fragmented one,
    ``ping`` pings first, ``close`` closes."""

    async def handler(ws: ServerConnection) -> None:
        async for message in ws:
            if message == "big":
                await ws.send("x" * 200_000)
            elif message == "parts":
                await ws.send(iter(["frag", "mented"]))
            elif message == "ping":
                await ws.ping()
                await ws.send("after ping")
            elif message == "close":
                await ws.close()
            else:
                await ws.send(message)

    async with serve(handler, "127.0.0.1", 0) as running:
        port = next(iter(running.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}/devtools/browser/abc"


async def test_messages_go_both_ways(server: str) -> None:
    ws = await cdp_socket.connect(server)
    await ws.send('{"id": 1, "method": "Target.getTargets"}')
    await ws.send("é unicode ✓")

    assert await ws.recv() == '{"id": 1, "method": "Target.getTargets"}'
    assert await ws.recv() == "é unicode ✓"
    await ws.close()


async def test_a_large_message_and_a_fragmented_one_arrive_whole(server: str) -> None:
    ws = await cdp_socket.connect(server)
    await ws.send("big")
    await ws.send("parts")
    await ws.send("y" * 70_000)  # a client frame with a 64-bit length

    assert await ws.recv() == "x" * 200_000
    assert await ws.recv() == "fragmented"
    assert await ws.recv() == "y" * 70_000
    await ws.close()


async def test_a_ping_is_answered_and_a_close_ends_iteration(server: str) -> None:
    ws = await cdp_socket.connect(server)
    await ws.send("ping")
    assert await ws.recv() == "after ping"

    await ws.send("close")
    seen = [message async for message in ws]

    assert seen == []
    assert ws.closed
    with pytest.raises(cdp_socket.ConnectionClosed):
        await ws.send("after close")


async def test_a_message_over_the_cap_closes_the_connection(server: str) -> None:
    ws = await cdp_socket.connect(server, max_size=1000)
    await ws.send("big")

    with pytest.raises(cdp_socket.ConnectionClosed, match="over 1000 bytes"):
        await ws.recv()


async def test_a_refused_handshake_says_so() -> None:
    async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    plain = await asyncio.start_server(http, "127.0.0.1", 0)
    port: Any = plain.sockets[0].getsockname()[1]
    async with plain:
        with pytest.raises(cdp_socket.ConnectionClosed, match="403"):
            await cdp_socket.connect(f"ws://127.0.0.1:{port}/")


async def test_only_websocket_urls_are_dialled() -> None:
    with pytest.raises(ValueError, match="not a WebSocket URL"):
        await cdp_socket.connect("http://127.0.0.1:9222/json/version")
