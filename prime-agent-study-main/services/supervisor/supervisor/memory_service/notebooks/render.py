"""How facts are cleaned on the way in and rendered on the way out (QM notebook format)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime

from supervisor.memory_service.notebooks.types import Candidate, Fact

_SPACE = re.compile(r"\s+")
_BULLET = re.compile(r"^(?:[-*](?:\s+|$))+")  # bullets, or a bare one: every one, so cleaning twice changes nothing
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
        text = _SPACE.sub(" ", unicodedata.normalize("NFC", raw)).strip()
        text = _BULLET.sub("", text)
        text = _DATED.sub(lambda m: f"on {m.group(1)}: ", text)
        text = _SAID_IN.sub(lambda m: f" [claimed source: {m.group(1)}]", text)
        if text:
            out.append(text)
    return out


def edited_text(text: str) -> str:
    text = _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()
    return _DATED.sub("", _BULLET.sub("", text)).strip()


def normalize(text: str) -> str:
    """The key two facts are the same fact by: compatibility-composed Unicode, case
    folded, whitespace collapsed ("Zürich" typed either way, "ß" and "ss" alike)."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def bullets(body: str) -> list[str]:
    """The text of each markdown bullet line (model output parsing)."""
    return [
        line.lstrip()[2:].strip()
        for line in body.splitlines()
        if line.lstrip().startswith(("- ", "* "))
    ]


def dates(fact: Fact) -> str:
    """When the fact was first remembered, and when it last changed if on a later day
    (mem0 keeps both, created_at and updated_at, on every memory): an edited fact must not
    look as old as the text it replaced."""
    created, updated = date_str(fact.created_at), date_str(fact.updated_at or fact.created_at)
    out = created if updated <= created else f"{created}, updated {updated}"
    # Graphiti gives a fact its valid range; a time-bound fact shows when it ends.
    return f"{out}, until {fact.expires_on}" if fact.expires_on else out


def current(facts: Iterable[Fact], today: str | None = None) -> list[Fact]:
    """The facts not past their expiry: what the agent recalls, searches and reads."""
    return [f for f in facts if not f.expired(today)]


def render_facts(facts: Iterable[Fact]) -> str:
    return "\n".join(f"- ({dates(f)}) {f.text}" for f in facts)


def fact_line(fact: Fact) -> str:
    """The form tools show: ids let the agent edit or delete a fact."""
    return f"#{fact.id} ({dates(fact)}) {fact.text}"


def parse_fact_id(value: str) -> str:
    return value.strip().removeprefix("#")


def fact_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def candidate(fact: Fact, score: float) -> Candidate:
    return Candidate(
        render_facts([fact]), score, id=fact.id, at=fact.created_at, provenance=fact.provenance
    )


def query_facts(facts: Iterable[Fact], q: str, limit: int) -> list[str]:
    """Every term must appear (case-insensitive); oldest first, as fact lines."""
    terms = [t for t in q.lower().split() if t]
    if not terms:
        return []
    hits = [f for f in facts if all(t in f.text.lower() for t in terms)]
    return [fact_line(f) for f in hits[:limit]]
