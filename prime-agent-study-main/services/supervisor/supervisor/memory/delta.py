"""Recall deltas: after the first run, inject only new or changed facts (QM recall-delta).

The model keeps what earlier runs injected (it is in the conversation), so a run
injects only what it does not already hold. A fact the model holds that is no
longer recalled stays valid if it is unchanged in its notebook; otherwise the
model is told how many earlier facts were consolidated or removed, or that some
sources are no longer authorized, so it stops relying on them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from supervisor.memory.cross import Recalled, render_recall
from supervisor.memory_service.notebooks.render import fact_hash, render_facts
from supervisor.memory_service.notebooks.types import Fact, Provenance, Sensitivity


@dataclass(frozen=True, slots=True)
class Shown:
    """One fact the model holds: where it came from and what text it saw."""

    notebook: str
    id: str | None
    hash: str
    provenance: Provenance | None = None

    @property
    def key(self) -> tuple[str, str | None, str]:
        return (self.notebook, self.id, self.hash)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"notebook": self.notebook, "id": self.id, "hash": self.hash}
        if self.provenance is not None:
            out |= {
                "sensitivity": self.provenance.sensitivity.value,
                "sources": list(self.provenance.sources),
                "source_unknown": self.provenance.source_unknown,
            }
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Shown:
        provenance = None
        if "sensitivity" in data:
            provenance = Provenance(
                Sensitivity.parse(data["sensitivity"]),
                tuple(data.get("sources") or ()),
                bool(data.get("source_unknown")),
            )
        return cls(str(data["notebook"]), data.get("id"), str(data["hash"]), provenance)


def short_hash(text: str) -> str:
    return fact_hash(text)[:16]


def shown_of(fact: Recalled) -> Shown:
    return Shown(fact.notebook_id, fact.id, short_hash(fact.text), fact.provenance)


def held_hashes(facts: Sequence[Fact]) -> dict[str, str]:
    """{fact id: hash of its rendered text}: the facts a notebook currently holds."""
    return {f.id: short_hash(render_facts([f])) for f in facts}


@dataclass(frozen=True, slots=True)
class Delta:
    text: str
    shown: list[Shown]


def recall_delta(
    recalled: Sequence[Recalled],
    previous: Sequence[Shown] | None,
    held: Mapping[str, Mapping[str, str]],
    labels: Mapping[str, str] | None = None,
) -> Delta:
    """What to inject now, and every fact the model will hold afterwards.

    ``previous`` is the last retained record (None: nothing held, inject all).
    ``held`` maps each still-readable notebook to its current {id: hash}.
    """
    labels = labels or {}
    current = [shown_of(f) for f in recalled]
    if previous is None:
        return Delta(render_recall(recalled, labels), current)
    before = {s.key: s for s in previous}
    now = {s.key for s in current}
    added = [f for f, s in zip(recalled, current, strict=True) if s.key not in before]
    shown = list(current)
    removed: dict[str, int] = {}
    for key, s in before.items():
        if key in now:
            continue
        if s.id is not None and held.get(s.notebook, {}).get(s.id) == s.hash:
            shown.append(s)  # not re-recalled, but unchanged: the model may keep relying on it
        else:
            removed[s.notebook] = removed.get(s.notebook, 0) + 1
    withdrawn = [(nb, n) for nb, n in removed.items() if nb in held]
    parts = []
    if added:
        parts.append(f"New or updated memory facts:\n\n{render_recall(added, labels)}")
    for nb, n in withdrawn:
        noun = "fact was" if n == 1 else "facts were"
        parts.append(
            f"{labels.get(nb, nb)}: {n} earlier {noun} consolidated or removed; "
            "rely only on the current list."
        )
    if len(removed) > len(withdrawn):
        parts.append(
            "Some previously recalled memory sources are no longer included. Do not rely on "
            "memory from sources that are not currently authorized."
        )
    return Delta("\n\n".join(parts), shown)
