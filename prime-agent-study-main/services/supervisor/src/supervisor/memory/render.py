"""How facts are cleaned on the way in and rendered on the way out (QM notebook format)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime

from supervisor.memory.types import Candidate, Fact

_SPACE = re.compile(r"\s+")
_BULLET = re.compile(r"^[-*]\s+")
_DATED = re.compile(r"^\((\d{4}-\d\d-\d\d)\)\s*")
_SAID_IN = re.compile(r"\s+\(said in ([^)]+)\)\s*$", re.IGNORECASE)


def date_str(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def clean_captured(facts: Iterable[str]) -> list[str]:
    """One line per fact; a leading date becomes prose, a claimed source is labelled.

    "(said in X)" is a model's claim, not provenance, so it is kept as text marked
    as a claim; real sources come from runtime context only.
    """
    out: list[str] = []
    for raw in facts:
        text = _SPACE.sub(" ", raw).strip()
        text = _BULLET.sub("", text)
        text = _DATED.sub(lambda m: f"on {m.group(1)}: ", text)
        text = _SAID_IN.sub(lambda m: f" [claimed source: {m.group(1)}]", text)
        if text:
            out.append(text)
    return out


def edited_text(text: str) -> str:
    return _DATED.sub("", _BULLET.sub("", _SPACE.sub(" ", text).strip()))


def normalize(text: str) -> str:
    return text.strip().lower()


def bullets(body: str) -> list[str]:
    """The text of each markdown bullet line (model output parsing)."""
    return [
        line.lstrip()[2:].strip()
        for line in body.splitlines()
        if line.lstrip().startswith(("- ", "* "))
    ]


def render_facts(facts: Iterable[Fact]) -> str:
    return "\n".join(f"- ({date_str(f.created_at)}) {f.text}" for f in facts)


def fact_line(fact: Fact) -> str:
    """The form tools show: ids let the agent edit or delete a fact."""
    return f"#{fact.id} ({date_str(fact.created_at)}) {fact.text}"


def parse_fact_id(value: str) -> str:
    return value.strip().removeprefix("#")


def fact_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def candidate(fact: Fact, score: float) -> Candidate:
    return Candidate(
        render_facts([fact]), score, id=fact.id, at=fact.created_at, provenance=fact.provenance
    )


def recent_candidates(facts: Iterable[Fact]) -> list[Candidate]:
    """Newest first at a flat score: the fallback ranking without embeddings."""
    return [candidate(f, 0.2) for f in reversed(list(facts))]


def query_facts(facts: Iterable[Fact], q: str, limit: int) -> list[str]:
    """Every term must appear (case-insensitive); oldest first, as fact lines."""
    terms = [t for t in q.lower().split() if t]
    if not terms:
        return []
    hits = [f for f in facts if all(t in f.text.lower() for t in terms)]
    return [fact_line(f) for f in hits[:limit]]
