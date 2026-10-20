"""Recall and search across a session's notebooks (QM's memory/cross-scope).

Recall ranks every readable notebook's candidates together and fits them into a
character budget (6,000 by default), one ``### <notebook>`` heading per notebook.
Search interleaves the notebooks' hits round-robin so no single notebook crowds
out the others.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from supervisor.memory.render import fact_line
from supervisor.memory.service import MemoryService
from supervisor.memory.types import RECALL_MAX_CHARS, Provenance, RecallContext


@dataclass(frozen=True, slots=True)
class Recalled:
    notebook_id: str
    text: str
    id: str | None = None
    provenance: Provenance | None = None


def _heading(notebook_id: str, labels: Mapping[str, str]) -> str:
    return f"### {labels.get(notebook_id, notebook_id)}"


async def recall_across(
    memory: MemoryService,
    notebooks: Sequence[str],
    context: RecallContext,
    labels: Mapping[str, str] | None = None,
) -> list[Recalled]:
    """The best-ranked facts that fit the budget, headings included in the cost."""
    labels = labels or {}
    budget = max(0, min(RECALL_MAX_CHARS, int(context.max_chars)))
    if not budget:
        return []
    unique = list(dict.fromkeys(notebooks))
    batches = await asyncio.gather(*(memory.recall(nb, context) for nb in unique))
    ranked = sorted(
        ((nb, row) for nb, rows in zip(unique, batches, strict=True) for row in rows),
        key=lambda item: (-item[1].score, -(item[1].at or 0)),
    )
    picked: list[Recalled] = []
    headed: set[str] = set()
    seen: set[str] = set()
    size = 0
    for nb, row in ranked:
        full = row.text
        if not full.strip() or full in seen:
            continue
        overhead = 1 if nb in headed else (2 if headed else 0) + len(_heading(nb, labels)) + 1
        # A provider block (no id) may be cut to fit; a fact is all or nothing.
        text = full if row.id is not None else full[: max(0, budget - size - overhead)]
        cost = len(text) + overhead
        if not text.strip() or size + cost > budget:
            continue
        size += cost
        seen.add(full)
        headed.add(nb)
        picked.append(Recalled(nb, text, row.id, row.provenance))
    return picked


def render_recall(facts: Sequence[Recalled], labels: Mapping[str, str] | None = None) -> str:
    labels = labels or {}
    groups: dict[str, list[str]] = {}
    for fact in facts:
        groups.setdefault(fact.notebook_id, []).append(fact.text)
    return "\n\n".join(f"{_heading(nb, labels)}\n" + "\n".join(rows) for nb, rows in groups.items())


async def search_across(
    memory: MemoryService,
    notebooks: Sequence[str],
    query: str,
    limit: int = 20,
    context: RecallContext | None = None,
) -> list[tuple[str, str]]:
    """(notebook_id, fact line) pairs, interleaved across notebooks, at most ``limit``."""
    count = max(1, min(50, int(limit)))
    unique = list(dict.fromkeys(notebooks))
    batches = await asyncio.gather(*(memory.query(nb, query, count, context) for nb in unique))
    out: list[tuple[str, str]] = []
    for i in range(count):
        for nb, hits in zip(unique, batches, strict=True):
            if len(out) >= count:
                return out
            if i < len(hits):
                out.append((nb, hits[i]))
    return out


async def read_notebook(memory: MemoryService, notebook_id: str) -> str:
    """Fact lines with ids (so the agent can edit), or the provider's rendering."""
    if memory.manages:
        return "\n".join(fact_line(f) for f in await memory.facts(notebook_id))
    return await memory.read(notebook_id)
