"""Tier 2 types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from supervisor.memory_service.types import MemoryError, Scope


@dataclass(frozen=True)
class NewMemoryItem:
    content: str
    confidence: float = 0.8
    source_session: str | None = None
    source_seq: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class MemoryItem:
    id: str
    scope: Scope
    content: str
    confidence: float
    source_session: str | None
    source_seq: int | None
    superseded_by: str | None
    valid_from_ms: int
    created_at_ms: int


@dataclass(frozen=True)
class TokenBudget:
    max_tokens: int

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("TokenBudget.max_tokens must be > 0")


@dataclass(frozen=True)
class ForgetSelector:
    item_id: str | None = None
    source_session: str | None = None
    all_in_scope: bool = False


@dataclass(frozen=True)
class RecallResult:
    items: tuple[MemoryItem, ...]
    item_ids: tuple[str, ...]
    tokens_used: int


class ItemNotFound(MemoryError):
    code = "ITEM_NOT_FOUND"


def estimate_tokens(text: str) -> int:
    """Cheap chars/4 estimate — packing authority stays in the service."""
    return max(1, len(text) // 4)
