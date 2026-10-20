"""Core types — same shapes API → storage (no conversion DTO)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FORMAT_VERSION = 1
SUPPORTED_FORMAT_VERSIONS = frozenset({FORMAT_VERSION})

EVENT_TYPES = frozenset(
    {
        "user/message",
        "assistant/message",
        "tool/call",
        "tool/result",
        "step/start",
        "step/end",
        "turn/end",
        "context/inject",
        "memory/recall",
    }
)


@dataclass(frozen=True)
class Scope:
    tenant: str
    subject: str
    namespace: str

    def __post_init__(self) -> None:
        if not self.tenant or not self.subject or not self.namespace:
            raise ValueError("SCOPE_REQUIRED: tenant, subject, and namespace are required")


@dataclass(frozen=True)
class SessionEvent:
    """Canonical log event — reused verbatim from API to storage."""

    type: str
    seq: int
    time: int
    data: Any = None


@dataclass(frozen=True)
class SessionHandle:
    id: str
    scope: Scope
    format_version: int = FORMAT_VERSION


@dataclass(frozen=True)
class SessionSummary:
    id: str
    scope: Scope
    status: str
    created_at: int
    event_count: int


@dataclass(frozen=True)
class LogicalSession:
    handle: SessionHandle
    status: str
    events: tuple[SessionEvent, ...]
    parent_id: str | None = None
    forked_at_seq: int | None = None
    created_at: int = 0
    closed_at: int | None = None
    repaired: bool = False


class MemoryError(Exception):
    code: str = "MEMORY_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class SessionNotFound(MemoryError):
    code = "SESSION_NOT_FOUND"


class SessionCorrupt(MemoryError):
    code = "SESSION_CORRUPT"


class FormatUnsupported(MemoryError):
    code = "FORMAT_UNSUPPORTED"


class StaleFence(MemoryError):
    code = "STALE_FENCE"


class LeaseHeld(MemoryError):
    code = "LEASE_HELD"


class ScopeRequired(MemoryError):
    code = "SCOPE_REQUIRED"
