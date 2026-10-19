"""Unit tests for binary LF JSONL framing (write first — red)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.agent.runtimes.harness.framing import read_frames


class FakeStream:
    """Returns fixed chunks, then b\"\"."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._i = 0

    async def read(self, n: int) -> bytes:
        if self._i >= len(self._chunks):
            return b""
        chunk = self._chunks[self._i]
        self._i += 1
        # Honour n so callers that read small buffers still work.
        if n > 0 and len(chunk) > n:
            # Put the remainder back as the next chunk.
            self._chunks.insert(self._i, chunk[n:])
            return chunk[:n]
        return chunk


async def collect(chunks: list[bytes]) -> list[dict]:
    return [frame async for frame in read_frames(FakeStream(chunks))]


@pytest.mark.asyncio
async def test_single_frame() -> None:
    frames = await collect([b'{"a":1}\n'])
    assert frames == [{"a": 1}]


@pytest.mark.asyncio
async def test_two_frames_in_one_chunk() -> None:
    frames = await collect([b'{"a":1}\n{"b":2}\n'])
    assert frames == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_frame_split_across_chunks() -> None:
    frames = await collect([b'{"a":', b"1}\n"])
    assert frames == [{"a": 1}]


@pytest.mark.asyncio
async def test_trailing_frame_no_newline() -> None:
    frames = await collect([b'{"a":1}'])
    assert frames == [{"a": 1}]


@pytest.mark.asyncio
async def test_empty_stream() -> None:
    frames = await collect([])
    assert frames == []


@pytest.mark.asyncio
async def test_blank_lines_ignored() -> None:
    frames = await collect([b'{"a":1}\n\n\n{"b":2}\n'])
    assert frames == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_u2028_u2029_inside_string() -> None:
    # U+2028 / U+2029 are legal inside JSON strings; must NOT split frames.
    payload = {"text": "line1\u2028line2\u2029line3"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    assert b"\xe2\x80\xa8" in raw  # U+2028 utf-8
    assert b"\xe2\x80\xa9" in raw  # U+2029 utf-8
    frames = await collect([raw])
    assert frames == [payload]


@pytest.mark.asyncio
async def test_crlf_stripped() -> None:
    frames = await collect([b'{"a":1}\r\n{"b":2}\r\n'])
    assert frames == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_1mb_frame_across_many_chunks() -> None:
    big = "x" * (1024 * 1024)
    obj = {"blob": big}
    raw = json.dumps(obj).encode("utf-8") + b"\n"
    # Deliberately tiny chunks so the frame spans many reads.
    chunks = [raw[i : i + 4096] for i in range(0, len(raw), 4096)]
    frames = await collect(chunks)
    assert frames == [obj]


@pytest.mark.asyncio
async def test_non_ascii_round_trips() -> None:
    payload = {"msg": "こんにちは🌍"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    frames = await collect([raw])
    assert frames == [payload]


@pytest.mark.asyncio
async def test_multibyte_char_split_across_chunks() -> None:
    # € is U+20AC → utf-8 e2 82 ac — split after first byte.
    payload = {"c": "€"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    euro = "€".encode()
    idx = raw.index(euro)
    chunks = [raw[: idx + 1], raw[idx + 1 :]]
    assert len(euro) == 3
    frames = await collect(chunks)
    assert frames == [payload]


@pytest.mark.asyncio
async def test_malformed_frame_raises() -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        await collect([b"{not-json}\n"])


@pytest.mark.asyncio
async def test_good_frames_yielded_before_bad_one() -> None:
    stream = FakeStream([b'{"ok":1}\n{bad}\n{"never":1}\n'])
    gen = read_frames(stream)
    first = await gen.__anext__()
    assert first == {"ok": 1}
    with pytest.raises((json.JSONDecodeError, ValueError)):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_fixture_pi_events_object_count_matches_wc_l() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "harness-events.jsonl"
    data = path.read_bytes()
    line_count = sum(1 for line in data.split(b"\n") if line.strip())
    # File may end with a final newline; wc -l counts newline-terminated lines.
    wc_l = data.count(b"\n")
    frames = await collect([data])
    assert len(frames) == wc_l
    assert len(frames) == line_count
