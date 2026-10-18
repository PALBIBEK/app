"""Markdown bullet recall — degrade never fail run."""

from __future__ import annotations

import re
from pathlib import Path

RECALL_HEADER = (
    "Relevant notes recalled from memory (not part of the user's message):"
)

DEFAULT_MIN_SCORE = 0.35


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-z0-9]+", text) if len(t) > 1}


def _score(query: str, snippet: str) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    s_tokens = _tokenize(snippet)
    if not s_tokens:
        return 0.0
    overlap = len(q_tokens & s_tokens)
    return overlap / len(q_tokens)


def _bullets_from_markdown(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
        elif line.startswith("* "):
            lines.append(line[2:].strip())
    return lines


def recall_markdown_bullets(
    memory_path: Path,
    query: str,
    *,
    limit: int = 5,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[str]:
    """Return ranked bullet snippets for a query; empty on any failure."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        if not memory_path.is_file():
            return []
        text = memory_path.read_text(encoding="utf-8")
    except OSError:
        return []
    scored: list[tuple[float, str]] = []
    for bullet in _bullets_from_markdown(text):
        score = _score(query, bullet)
        if score >= min_score:
            scored.append((score, bullet))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [snippet for _, snippet in scored[:limit]]


def recall_block_from_bullets(hits: list[str]) -> str:
    if not hits:
        return ""
    lines = [RECALL_HEADER]
    lines += [f"- {h.strip()}" for h in hits]
    return "\n".join(lines)
