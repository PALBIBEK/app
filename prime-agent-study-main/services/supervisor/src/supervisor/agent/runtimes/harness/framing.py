"""Binary LF JSONL framing — bytes in, dicts out. Nothing else."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol


class ByteStream(Protocol):
    async def read(self, n: int) -> bytes: ...


def _parse_frame(frame: bytes) -> dict[str, Any] | None:
    """Decode one frame. Return None for a blank line. Raise on bad JSON."""
    if frame.endswith(b"\r"):
        frame = frame[:-1]
    if not frame:
        return None
    obj = json.loads(frame.decode("utf-8"))
    if not isinstance(obj, dict):
        # A malformed frame is bad input from the child process, not a caller bug.
        raise ValueError(f"frame root must be a JSON object, got {type(obj).__name__}")  # noqa: TRY004
    return obj


async def read_frames(stream: ByteStream) -> AsyncIterator[dict[str, Any]]:
    """Yield JSON objects from a binary LF-delimited stream.

    Split on ``b"\\n"`` only. Decode and parse per complete frame — never per
    chunk — so a UTF-8 character straddling a pipe boundary cannot break us,
    and U+2028/U+2029 inside JSON strings cannot tear a frame apart.
    """
    buf = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            frame = bytes(buf[:nl])
            del buf[: nl + 1]
            parsed = _parse_frame(frame)
            if parsed is not None:
                yield parsed

    if buf:
        parsed = _parse_frame(bytes(buf))
        if parsed is not None:
            yield parsed
