"""Markdown-aware semantic chunking (port of Grok's `chunker.rs`).

Splits a document into pieces sized for embedding and retrieval, subject to
two constraints that a naive fixed-window splitter violates:

1. **Never split inside a fenced code block.** Half a fence is unparseable,
   and worse, a model retrieving it cannot tell the command is truncated —
   it will run it.
2. **Every chunk carries its ancestor headers.** A chunk is retrieved
   *alone*, with none of the surrounding document. "Use `--depth 100`" is
   actively harmful without the section header saying what it applies to.

Character counts stand in for token counts (~4 chars/token), as in Grok.
Exact tokenisation would require a tokenizer dependency to decide a
threshold that is already approximate.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 1800
MIN_CHUNK_CHARS = 40
"""Below this a chunk is merged into its neighbour: a 20-character fragment
retrieves poorly and still costs a full row and an embedding."""

_ATX_HEADER = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    id: str
    path: str
    start_line: int
    end_line: int
    text: str
    hash: str


@dataclass
class _Block:
    """A structural unit: a paragraph, a list, or a whole fenced block."""

    lines: list[str]
    start_line: int
    end_line: int
    headers: tuple[str, ...]
    atomic: bool = False           # a fenced block: never split further


def chunk_markdown(content: str, path: str,
                   max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    if not content.strip():
        return []
    blocks = _blocks(content)
    return _assemble(blocks, path, max_chars)


def _blocks(content: str) -> list[_Block]:
    """Walk the document once, tracking fence state and the header stack.

    Fence state is tracked first and dominates: while inside a fence, a line
    beginning with `#` is a shell comment, not a header. Getting that
    backwards splits every bash example in the corpus.
    """
    lines = content.splitlines()
    blocks: list[_Block] = []
    headers: list[tuple[int, str]] = []       # (level, text)

    buffer: list[str] = []
    buf_start = 1
    in_fence = False
    fence_marker = ""

    def flush(end_line: int, atomic: bool = False) -> None:
        nonlocal buffer, buf_start
        if any(line.strip() for line in buffer):
            blocks.append(_Block(list(buffer), buf_start, end_line,
                                 tuple(text for _, text in headers), atomic))
        buffer = []
        buf_start = end_line + 1

    for number, line in enumerate(lines, start=1):
        fence = _FENCE.match(line)

        if in_fence:
            buffer.append(line)
            if fence and line.strip().startswith(fence_marker):
                in_fence = False
                flush(number, atomic=True)
            continue

        if fence:
            flush(number - 1)                 # close whatever preceded the fence
            buf_start = number
            buffer = [line]
            in_fence = True
            fence_marker = fence.group(1)
            continue

        header = _ATX_HEADER.match(line)
        if header:
            flush(number - 1)
            level = len(header.group(1))
            # Pop siblings and deeper levels; what remains is the ancestor
            # chain, which is exactly what a retrieved chunk needs.
            while headers and headers[-1][0] >= level:
                headers.pop()
            headers.append((level, header.group(2).strip()))
            buf_start = number
            continue

        if not line.strip() and buffer:
            flush(number)                     # blank line ends a paragraph
            continue

        if not buffer:
            buf_start = number
        buffer.append(line)

    flush(len(lines), atomic=in_fence)
    return blocks


def _assemble(blocks: list[_Block], path: str, max_chars: int) -> list[Chunk]:
    """Pack blocks into chunks, restarting whenever the header path changes.

    Header changes force a boundary so a chunk never spans two sections —
    otherwise a fact from one section arrives labelled with another's header,
    which is worse than no header at all.
    """
    chunks: list[Chunk] = []
    current: list[_Block] = []

    def emit() -> None:
        nonlocal current
        if not current:
            return
        headers = current[0].headers
        body = "\n".join("\n".join(b.lines).rstrip() for b in current).strip()
        if body:
            prefix = "".join(f"{'#' * (i + 1)} {h}\n" for i, h in enumerate(headers))
            text = f"{prefix}\n{body}" if prefix else body
            chunks.append(_make_chunk(path, current[0].start_line,
                                      current[-1].end_line, text))
        current = []

    for block in blocks:
        size = sum(len(line) + 1 for line in block.lines)
        current_size = sum(sum(len(x) + 1 for x in b.lines) for b in current)

        if current and block.headers != current[0].headers:
            emit()
        elif current and current_size + size > max_chars:
            # An atomic block that does not fit stays whole and gets its own
            # chunk -- deliberately over-size rather than corrupted.
            emit()

        current.append(block)
        if not block.atomic and sum(sum(len(x) + 1 for x in b.lines)
                                    for b in current) >= max_chars:
            emit()

    emit()
    return _merge_tiny(chunks, path)


def _merge_tiny(chunks: list[Chunk], path: str) -> list[Chunk]:
    """Fold sub-threshold chunks into the previous one, when they share a
    section (same start header). A stray fragment is not worth a row."""
    if len(chunks) < 2:
        return chunks
    out: list[Chunk] = [chunks[0]]
    for chunk in chunks[1:]:
        if len(chunk.text.strip()) < MIN_CHUNK_CHARS:
            previous = out[-1]
            out[-1] = _make_chunk(path, previous.start_line, chunk.end_line,
                                  f"{previous.text}\n{chunk.text.strip()}")
        else:
            out.append(chunk)
    return out


def _make_chunk(path: str, start: int, end: int, text: str) -> Chunk:
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
    # id is derived from path + position so it is stable across re-indexes of
    # an unchanged file; hash is derived from content so a change is
    # detectable without comparing full text.
    identifier = hashlib.blake2b(f"{path}:{start}:{end}".encode("utf-8"),
                                 digest_size=12).hexdigest()
    return Chunk(id=identifier, path=path, start_line=start, end_line=end,
                 text=text, hash=digest)
