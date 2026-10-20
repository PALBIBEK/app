"""Memory domain types: notebooks of facts with provenance (ported from QM's memory/facts).

A *notebook* is one named long-term memory owned by one principal for one exact
pinned agent version. A *fact* is one stable-id record in it. Every fact carries
provenance: how sensitive it is, which sources (audiences) it was learned from,
and whether its provenance is incomplete. Provenance only ever tightens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

# Prompt recall carries at most this many characters per turn, across notebooks.
RECALL_MAX_CHARS: Final = 6_000


class Sensitivity(StrEnum):
    """Ordered from least to most restricted; ``stricter`` picks the later one."""

    ORDINARY = "ordinary"
    UNKNOWN = "unknown"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    @classmethod
    def parse(cls, value: object) -> Sensitivity:
        """Anything unrecognised (missing, malformed, model noise) is ``unknown``."""
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.UNKNOWN


_ORDER: Final = list(Sensitivity)


def stricter(a: Sensitivity | None, b: Sensitivity) -> Sensitivity:
    return b if a is None or b.rank >= a.rank else a


def personal_source(principal_id: str) -> str:
    return f"personal:{principal_id}"


def scope_source(scope_id: str) -> str:
    return f"scope:{scope_id}"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a fact came from. ``sources`` are audience refs (``personal:``/``scope:``)."""

    sensitivity: Sensitivity
    sources: tuple[str, ...] = ()
    source_unknown: bool = False

    def same_as(self, other: Provenance) -> bool:
        return (
            self.sensitivity is other.sensitivity
            and self.source_unknown == other.source_unknown
            and set(self.sources) == set(other.sources)
        )

    @property
    def group_key(self) -> tuple[str, tuple[str, ...], bool]:
        """Facts with equal keys may share a consolidation prompt; others never do."""
        return (self.sensitivity.value, tuple(sorted(self.sources)), self.source_unknown)


# Context we cannot trace (untracked history, tools, attachments) taints a capture.
UNTRACKED = Provenance(Sensitivity.UNKNOWN, (), True)


def merge_provenance(
    items: tuple[Provenance, ...] | list[Provenance],
    sensitivity: Sensitivity = Sensitivity.ORDINARY,
) -> Provenance:
    """The union of sources, the strictest sensitivity, unknown if any part is unknown."""
    sources: dict[str, None] = {}
    for item in items:
        sensitivity = stricter(sensitivity, item.sensitivity)
        sources.update(dict.fromkeys(item.sources))
    return Provenance(sensitivity, tuple(sources), any(item.source_unknown for item in items))


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    """Runtime context of a capture; never model-authored claims about sources.

    ``inherited`` is the provenance of everything the capture could depend on
    (recalled facts). ``None`` means it is not tracked, which makes the capture's
    provenance unknown rather than guessing it is clean.
    """

    source: str | None = None
    session_id: str | None = None
    sensitivity: Sensitivity | None = None
    inherited: tuple[Provenance, ...] | None = None


def capture_provenance(home: str, context: CaptureMetadata | None) -> Provenance:
    inherited = context.inherited if context else None
    merged = merge_provenance(
        inherited or (),
        (context.sensitivity if context and context.sensitivity else Sensitivity.UNKNOWN),
    )
    source = (context.source if context else None) or home
    sources = merged.sources if source in merged.sources else (*merged.sources, source)
    return Provenance(merged.sensitivity, sources, merged.source_unknown or inherited is None)


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    text: str
    provenance: Provenance
    created_at: int  # epoch ms
    updated_at: int
    author: str | None = None


@dataclass(frozen=True, slots=True)
class Notebook:
    id: str
    tenant_id: str
    owner: str  # principal id
    agent_pin: str
    name: str
    label: str | None
    revision: int  # bumped once per changing transaction
    created_at: int
    archived_at: int | None = None
    last_tidied_at: int | None = None
    agent: str | None = None
    """The agent's name (survives new versions): where its facts sit in the semantic bank."""

    @property
    def home(self) -> str:
        """The source a notebook's own facts default to: its owner's personal audience."""
        return personal_source(self.owner)

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass(frozen=True, slots=True)
class Candidate:
    """A recall row: a rendered fact (or provider block) with a ranking score."""

    text: str
    score: float
    id: str | None = None
    at: int | None = None
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Edit:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Revision:
    """One change to one fact; the notebook's history is the ordered list of these."""

    revision: int
    fact_id: str
    op: str  # insert | update | delete | restore
    text: str
    provenance: Provenance
    at: int
    author: str | None = None


@dataclass(frozen=True, slots=True)
class NotebookStats:
    facts: int
    bytes: int
    updated_at: int | None = None


@dataclass(slots=True)
class RecallContext:
    query: str | None = None
    actor: str | None = None
    session_id: str | None = None
    max_chars: int = RECALL_MAX_CHARS
    extra: dict[str, object] = field(default_factory=dict)


NOTEBOOK_ID_SEP = "~"


def notebook_id(tenant_id: str) -> str:
    """A new notebook id: ``<tenant>~<random hex>``.

    It names its tenant so any worker can address the memory service on that
    tenant's behalf without a lookup; the service still checks the notebook's
    stored tenant against the caller's, so a forged prefix finds nothing.
    """
    import uuid

    return f"{tenant_id}{NOTEBOOK_ID_SEP}{uuid.uuid4().hex}"


def tenant_of(notebook_id: str) -> str:
    """The tenant a notebook id names (``""`` for an id without one)."""
    tenant, sep, _ = notebook_id.rpartition(NOTEBOOK_ID_SEP)
    return tenant if sep else ""
