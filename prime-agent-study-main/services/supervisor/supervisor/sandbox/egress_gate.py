"""The egress gate: a ``local_docker`` sandbox with ``network: allow`` reaches the open
network only through it, and only to the hosts it lets by.

It runs in a sidecar container on two networks: the sandbox's own, which has no route
out, and the outside one. In the sandbox each allowed host resolves to the gate. The gate
reads which host a connection is for, from the TLS ClientHello's server name (SNI) on port
443 or the ``Host`` header on port 80, and forwards the connection, unread past that,
only when the host is allowed: it terminates no TLS, so it holds no certificate and sees
no content, and any client works through it (a browser, a CDP WebSocket, curl).

Shipped as source and run with the sidecar's own Python: the standard library only.

    python3 egress_gate.py host-a.example host-b.example
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

PEEK_BYTES = 16 * 1024
"""Enough for any ClientHello or request head; nothing past it is read for the host."""
PEEK_TIMEOUT_S = 10.0


def server_name(hello: bytes) -> str | None:
    """The SNI host of a TLS ClientHello, or None if it is not one, or names none."""
    try:
        if hello[0] != 0x16 or hello[5] != 0x01:  # a handshake record, a ClientHello
            return None
        pos = 9 + 2 + 32  # record (5) and handshake (4) headers, version, random
        pos += 1 + hello[pos]  # session id
        pos += 2 + int.from_bytes(hello[pos : pos + 2], "big")  # cipher suites
        pos += 1 + hello[pos]  # compression methods
        end = pos + 2 + int.from_bytes(hello[pos : pos + 2], "big")
        pos += 2
        while pos + 4 <= end:
            kind, size = int.from_bytes(hello[pos : pos + 2], "big"), int.from_bytes(hello[pos + 2 : pos + 4], "big")
            if kind == 0:  # server_name: a list, then (type, length, name)
                length = int.from_bytes(hello[pos + 7 : pos + 9], "big")
                return hello[pos + 9 : pos + 9 + length].decode("ascii").lower()
            pos += 4 + size
    except (IndexError, UnicodeDecodeError):
        return None
    return None


def http_host(head: bytes) -> str | None:
    """The ``Host`` of a plain HTTP request head, without its port."""
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"host":
            return value.strip().decode("ascii", "replace").rsplit(":", 1)[0].lower() or None
    return None


async def _peek(reader: asyncio.StreamReader, port: int) -> bytes:
    """The first bytes of a connection: a whole ClientHello record, or a request head."""
    data = await asyncio.wait_for(reader.read(PEEK_BYTES), PEEK_TIMEOUT_S)
    if port == 443:
        while len(data) >= 5 and len(data) < 5 + int.from_bytes(data[3:5], "big") and len(data) < PEEK_BYTES:
            more = await asyncio.wait_for(reader.read(PEEK_BYTES - len(data)), PEEK_TIMEOUT_S)
            if not more:
                break
            data += more
    else:
        while b"\r\n\r\n" not in data and len(data) < PEEK_BYTES:
            more = await asyncio.wait_for(reader.read(PEEK_BYTES - len(data)), PEEK_TIMEOUT_S)
            if not more:
                break
            data += more
    return data


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    finally:
        with contextlib.suppress(Exception):
            writer.close()


def handler(allowed: frozenset[str], port: int):  # type: ignore[no-untyped-def]
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await _peek(reader, port)
            host = server_name(first) if port == 443 else http_host(first)
            if host is None or host not in allowed:
                print(f"refused {host or '?'}:{port}", flush=True)
                return
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
            up_writer.write(first)
            await up_writer.drain()
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    return handle


async def main(hosts: list[str]) -> None:
    allowed = frozenset(h.lower() for h in hosts)
    servers = [await asyncio.start_server(handler(allowed, port), "0.0.0.0", port) for port in (443, 80)]
    print(f"egress gate: {', '.join(sorted(allowed))}", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
