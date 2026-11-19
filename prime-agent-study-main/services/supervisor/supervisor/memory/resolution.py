"""Resolve newly extracted facts against what a notebook already holds (write time).

Automatic capture only added: "Prefers tea" stayed live beside a later "Prefers coffee",
and a turn recalled both. This follows two systems that resolve at write time:

- Graphiti (getzep/graphiti, graphiti_core/prompts/dedupe_edges.py ``resolve_edge`` and
  utils/maintenance/edge_operations.py): for each new fact, the related existing facts
  (search limit 10) are judged by a model for ``duplicate_facts`` and
  ``contradicted_facts``, under strict rules (a contradiction needs the two facts to be
  unable to both be true; recency alone is not one). A contradicted fact is invalidated,
  not erased: it keeps its history.
- mem0 (mem0ai/mem0, mem0/memory/main.py add): the existing memories are retrieved once
  (top 10), given the model under small integer ids instead of real ones
  ("anti-hallucination"), and the model answers in one call.

Here a duplicate is not added, a contradicted fact is deleted from the notebook (its
revision history keeps it, with dates: the invalidation), and anything the model's
answer does not establish changes nothing. A failed or malformed answer resolves
nothing, and the facts are captured as before.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from supervisor.memory.classification import OneShot
from supervisor.memory_service.notebooks.rank import FLOOR, ranked_candidates
from supervisor.memory_service.notebooks.render import fact_line
from supervisor.memory_service.notebooks.types import Fact

logger = logging.getLogger(__name__)

RELATED_PER_FACT: Final = 10
"""Existing facts compared with each new one (Graphiti's DEFAULT_SEARCH_LIMIT, mem0's top_k)."""

RESOLUTION_PROMPT: Final = """You compare NEW FACTS about a user with EXISTING FACTS already remembered.

For each new fact decide:
- duplicates: existing facts that state the same information (nothing new in the new fact).
- contradicts: existing facts that the new fact makes false, so they should no longer be remembered.

Rules:
- NEVER mark facts as duplicates if they have key differences, particularly around numeric
  values, dates, or key qualifiers.
- Only mark a fact as contradicted when the NEW FACT and the existing fact cannot both be true.
  A contradiction requires explicit evidence that the old fact stopped being true or was
  replaced, such as clear termination, replacement, transfer, or mutually exclusive
  current-state language.
- Do NOT infer contradiction from recency alone, or from a new event involving the same
  people, projects or things.
- Use only the ids shown in EXISTING FACTS.

Answer with JSON only, one entry per new fact, in this shape:
{"facts": [{"new": 0, "duplicates": [], "contradicts": []}]}"""


@dataclass(frozen=True, slots=True)
class Resolution:
    """What to add (indexes into the new facts) and which existing fact ids to retire."""

    add: tuple[int, ...]
    retire: tuple[str, ...]


def related(existing: Sequence[Fact], new_facts: Sequence[str]) -> list[Fact]:
    """The existing facts that share content words with any new fact, at most
    RELATED_PER_FACT per new fact, each once."""
    out: dict[str, Fact] = {}
    by_id = {f.id: f for f in existing}
    for text in new_facts:
        ranked = [c for c in ranked_candidates(existing, f"Current message: {text}") if c.score > FLOOR]
        for row in ranked[:RELATED_PER_FACT]:
            if row.id is not None:
                out.setdefault(row.id, by_id[row.id])
    return sorted(out.values(), key=lambda f: int(f.id))


def prompt_body(new_facts: Sequence[str], candidates: Sequence[Fact]) -> str:
    existing = "\n".join(f"[{i}] {fact_line(f).split(' ', 1)[1]}" for i, f in enumerate(candidates))
    fresh = "\n".join(f"[{i}] {text}" for i, text in enumerate(new_facts))
    return f"EXISTING FACTS:\n{existing}\n\nNEW FACTS:\n{fresh}"


def parse(answer: str, new_count: int, candidates: Sequence[Fact]) -> Resolution | None:
    """The model's answer, or None when it is not the JSON asked for. Ids outside the lists
    shown are ignored: only what the answer establishes changes anything."""
    try:
        body = json.loads(_json_part(answer))
        entries = body["facts"]
        if not isinstance(entries, list):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    duplicate: set[int] = set()
    retire: dict[str, None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        new = _index(entry.get("new"), new_count)
        if new is None:
            continue
        if any(_index(i, len(candidates)) is not None for i in _list(entry.get("duplicates"))):
            duplicate.add(new)
        for i in _list(entry.get("contradicts")):
            if (idx := _index(i, len(candidates))) is not None:
                retire.setdefault(candidates[idx].id)
    return Resolution(tuple(i for i in range(new_count) if i not in duplicate), tuple(retire))


async def resolve(one_shot: OneShot, new_facts: Sequence[str], existing: Sequence[Fact]) -> Resolution:
    """Which new facts to add and which existing facts they retire. Without related facts
    no call is made; a failed or malformed answer keeps every new fact and retires none."""
    everything = Resolution(tuple(range(len(new_facts))), ())
    candidates = related(existing, new_facts)
    if not candidates:
        return everything
    try:
        answer = await one_shot(RESOLUTION_PROMPT, prompt_body(new_facts, candidates))
    except Exception:  # resolution is an aid: without it, capture is what it was
        logger.warning("memory resolution failed", exc_info=True)
        return everything
    resolved = parse(answer or "", len(new_facts), candidates)
    if resolved is None:
        logger.warning("memory resolution answer was not the JSON asked for")
        return everything
    return resolved


def _json_part(answer: str) -> str:
    start, end = answer.find("{"), answer.rfind("}")
    return answer[start : end + 1] if start != -1 and end > start else answer


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _index(value: Any, size: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value < size else None
