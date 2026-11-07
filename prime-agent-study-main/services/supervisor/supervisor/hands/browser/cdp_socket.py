"""A WebSocket client for the Chrome DevTools Protocol, from the standard library alone.

The CDP supervisor (``cdp_supervisor.py``) holds one long connection to the browser's DevTools
socket, as Hermes's does with the ``websockets`` package. The runtime runs on the
sandbox's own Python with nothing but Playwright installed, so this is the part of RFC 6455
a CDP client needs, in the shape of ``websockets``' client (``connect``, ``send``, ``recv``,
``async for``, ``close``): text frames (fragmented or not), pings answered, a close
handshake, ``ws://`` and ``wss://`` (a hosted browser's connect URL). Frames a server sends
are never masked; frames a client sends always are.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import ssl
import struct
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

__all__ = ["ConnectionClosed", "WebSocket", "connect"]

_GUID: Final = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TEXT, _BINARY, _CLOSE, _PING, _PONG, _CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0


class ConnectionClosed(ConnectionError):
    """The socket closed (by either side, or because it broke)."""


class WebSocket:
    """One open connection: ``send`` a text message, ``recv`` (or iterate) the next one."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, max_size: int | None
    ) -> None:
        self._in = reader
        self._out = writer
        self._max_size = max_size
        self._send_lock = asyncio.Lock()
        self.closed = False

    async def send(self, message: str) -> None:
        await self._frame(_TEXT, message.encode("utf-8"))

    async def recv(self) -> str:
        """The next text (or binary, decoded) message; ``ConnectionClosed`` once it closes."""
        parts: list[bytes] = []
        size = 0
        while True:
            fin, opcode, payload = await self._read_frame()
            if opcode == _PING:
                await self._frame(_PONG, payload)
                continue
            if opcode == _PONG:
                continue
            if opcode == _CLOSE:
                with contextlib.suppress(Exception):
                    await self._frame(_CLOSE, payload[:2])
                await self._shut()
                raise ConnectionClosed("the server closed the connection")
            size += len(payload)
            if self._max_size is not None and size > self._max_size:
                await self.close()
                raise ConnectionClosed(f"a message over {self._max_size} bytes")
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", errors="replace")

    def __aiter__(self) -> AsyncIterator[str]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[str]:
        while True:
            try:
                yield await self.recv()
            except ConnectionClosed:
                return

    async def close(self) -> None:
        if self.closed:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._frame(_CLOSE, struct.pack("!H", 1000)), timeout=2)
        await self._shut()

    async def _shut(self) -> None:
        self.closed = True
        self._out.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._out.wait_closed(), timeout=2)

    async def _frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise ConnectionClosed("the connection is closed")
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = _mask(payload, mask)
        async with self._send_lock:
            try:
                self._out.write(header + mask + masked)
                await self._out.drain()
            except (ConnectionError, OSError) as exc:
                self.closed = True
                raise ConnectionClosed(str(exc)) from exc

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            first, second = await self._in.readexactly(2)
            n = second & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", await self._in.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self._in.readexactly(8))
            mask = await self._in.readexactly(4) if second & 0x80 else b""
            payload = await self._in.readexactly(n) if n else b""
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            await self._shut()
            raise ConnectionClosed("the connection broke") from exc
        if mask:
            payload = _mask(payload, mask)
        return bool(first & 0x80), first & 0x0F, payload


def _mask(payload: bytes, mask: bytes) -> bytes:
    """``payload`` XOR the repeated 4-byte ``mask``, a word at a time."""
    n = len(payload)
    repeated = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(repeated, "big")).to_bytes(n, "big")


async def connect(
    url: str, *, max_size: int | None = 50 * 1024 * 1024, timeout: float = 10.0
) -> WebSocket:
    """An open WebSocket to ``url`` (``ws://`` or ``wss://``)."""
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise ValueError(f"not a WebSocket URL: {parts.scheme}://")
    secure = parts.scheme == "wss"
    host = parts.hostname or ""
    port = parts.port or (443 if secure else 80)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host, port, ssl=ssl.create_default_context() if secure else None, limit=2**20
        ),
        timeout=timeout,
    )
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    authority = host if parts.port is None else f"{host}:{port}"
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {authority}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
    )
    if parts.username:  # a provider's connect URL can carry its credentials as userinfo
        token = base64.b64encode(f"{parts.username}:{parts.password or ''}".encode()).decode(
            "ascii"
        )
        request += f"Authorization: Basic {token}\r\n"
    writer.write((request + "\r\n").encode("latin-1"))
    await writer.drain()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        writer.close()
        raise ConnectionClosed("the server did not answer the WebSocket handshake") from exc
    status, *lines = head.decode("latin-1").split("\r\n")
    if " 101 " not in f"{status} ":
        writer.close()
        raise ConnectionClosed(f"the WebSocket handshake was refused: {status.strip()}")
    headers = {
        k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines if line)
    }
    expected = base64.b64encode(hashlib.sha1(key.encode("ascii") + _GUID).digest()).decode("ascii")
    if headers.get("sec-websocket-accept") != expected:
        writer.close()
        raise ConnectionClosed("the server answered the WebSocket handshake wrongly")
    return WebSocket(reader, writer, max_size)
