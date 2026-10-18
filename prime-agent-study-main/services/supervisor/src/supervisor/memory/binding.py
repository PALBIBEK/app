"""Which memory a session uses: its owner, its exact agent version, its notebooks.

A session writes to one notebook and recalls from the notebooks it reads (QM's
``memoryAccess {read[], write}``), under QM's memory policy: recall ``off`` /
``writable`` (only the written notebook) / ``visible`` (all bound notebooks),
capture ``off`` / ``writable``, and a strategy (``per-turn`` automatic capture,
or ``agent-only`` where the agent is the sole curator). The binding is stored on
the session row, so a resumed or adopted session keeps exactly the same memory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from supervisor.harness.core.digests import content_digest
from supervisor.memory.store import NotebookStore

DEFAULT_NOTEBOOK = "default"
# QM per-turn capture: flush a burst after this much quiet, or after this many turns.
DEFAULT_CAPTURE_QUIET_S = 180.0
DEFAULT_CAPTURE_MAX_TURNS = 10


class RecallMode(StrEnum):
    OFF = "off"
    WRITABLE = "writable"
    VISIBLE = "visible"


class CaptureMode(StrEnum):
    OFF = "off"
    WRITABLE = "writable"


class Strategy(StrEnum):
    PER_TURN = "per-turn"
    AGENT_ONLY = "agent-only"


def agent_pin(
    *,
    harness_version: str,
    manifest_snapshot: dict[str, Any],
    composition_pins: dict[str, Any] | None,
) -> str:
    """The exact agent version a session pins; memory and history are keyed by it.

    A composition is pinned by its content digest; a bare harness by the digest of
    the manifest snapshot the session stores, so editing a harness file in place
    (same version string) still yields a different pin.
    """
    if composition_pins and composition_pins.get("composition_digest"):
        name = composition_pins.get("composition_id") or "composition"
        return f"composition:{name}@{composition_pins['composition_digest']}"
    return f"harness:{harness_version}@{content_digest(manifest_snapshot)}"


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    """What a client asks for at create, by notebook *name* (created on first use)."""

    write: str | None = DEFAULT_NOTEBOOK
    read: tuple[str, ...] = (DEFAULT_NOTEBOOK,)
    recall: RecallMode = RecallMode.VISIBLE
    capture: CaptureMode = CaptureMode.WRITABLE
    strategy: Strategy = Strategy.PER_TURN
    capture_quiet_s: float = DEFAULT_CAPTURE_QUIET_S
    capture_max_turns: int = DEFAULT_CAPTURE_MAX_TURNS
    open: bool = False

    def __post_init__(self) -> None:
        names = [*self.read, *([self.write] if self.write else [])]
        if any(not n or not n.strip() or len(n) > 64 for n in names):
            raise ValueError("notebook names must be 1-64 characters")
        if not 0 <= self.capture_quiet_s <= 86_400:
            raise ValueError("capture_quiet_s must be between 0 and 86400")
        if not 1 <= self.capture_max_turns <= 100:
            raise ValueError("capture_max_turns must be between 1 and 100")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MemoryRequest:
        """From a client body / workflow payload; absent keys take the defaults."""
        if not data:
            return cls()
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        if "read" in known:
            known["read"] = tuple(known["read"])
        for key, kind in (("recall", RecallMode), ("capture", CaptureMode), ("strategy", Strategy)):
            if key in known:
                known[key] = kind(known[key])
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "read": list(self.read)}


@dataclass(frozen=True, slots=True)
class MemoryBinding:
    """A session's resolved memory: notebook ids, never names (names can be reused)."""

    owner: str
    agent_pin: str
    write: str | None
    read: tuple[str, ...]
    recall: RecallMode = RecallMode.VISIBLE
    capture: CaptureMode = CaptureMode.WRITABLE
    strategy: Strategy = Strategy.PER_TURN
    capture_quiet_s: float = DEFAULT_CAPTURE_QUIET_S
    capture_max_turns: int = DEFAULT_CAPTURE_MAX_TURNS
    open: bool = False
    labels: dict[str, str] = field(default_factory=dict)  # notebook id -> display name

    @property
    def recall_notebooks(self) -> tuple[str, ...]:
        """QM's recallMemoryScopes: the written notebook first, then the readable ones."""
        match self.recall:
            case RecallMode.OFF:
                return ()
            case RecallMode.WRITABLE:
                return (self.write,) if self.write else ()
            case RecallMode.VISIBLE:
                return tuple(dict.fromkeys([*([self.write] if self.write else []), *self.read]))

    @property
    def readable(self) -> tuple[str, ...]:
        """Notebooks tools may read or search (recall policy aside)."""
        return tuple(dict.fromkeys([*([self.write] if self.write else []), *self.read]))

    @property
    def writable(self) -> str | None:
        return self.write if self.capture is CaptureMode.WRITABLE else None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "read": list(self.read)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryBinding:
        return cls(
            owner=str(data["owner"]),
            agent_pin=str(data["agent_pin"]),
            write=data.get("write"),
            read=tuple(data.get("read") or ()),
            recall=RecallMode(data.get("recall", RecallMode.VISIBLE)),
            capture=CaptureMode(data.get("capture", CaptureMode.WRITABLE)),
            strategy=Strategy(data.get("strategy", Strategy.PER_TURN)),
            capture_quiet_s=float(data.get("capture_quiet_s", DEFAULT_CAPTURE_QUIET_S)),
            capture_max_turns=int(data.get("capture_max_turns", DEFAULT_CAPTURE_MAX_TURNS)),
            open=bool(data.get("open", False)),
            labels=dict(data.get("labels") or {}),
        )


async def resolve_binding(
    store: NotebookStore,
    *,
    tenant_id: str,
    owner: str,
    pin: str,
    request: MemoryRequest | None,
) -> MemoryBinding:
    """Resolve names to this owner's notebooks for this pin, creating them on first use."""
    request = request or MemoryRequest()
    names = list(dict.fromkeys([*([request.write] if request.write else []), *request.read]))
    notebooks = {
        name: await store.ensure(tenant_id=tenant_id, owner=owner, agent_pin=pin, name=name)
        for name in names
    }
    return MemoryBinding(
        owner=owner,
        agent_pin=pin,
        write=notebooks[request.write].id if request.write else None,
        read=tuple(notebooks[n].id for n in request.read),
        recall=request.recall,
        capture=request.capture,
        strategy=request.strategy,
        capture_quiet_s=request.capture_quiet_s,
        capture_max_turns=request.capture_max_turns,
        open=request.open,
        labels={nb.id: nb.display for nb in notebooks.values()},
    )
