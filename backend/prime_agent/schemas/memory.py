"""Product Memory BFF schemas (D7 outer honesty).

Product metaphor → Durable Memory Scope mapping
------------------------------------------------
Outer ``/memory`` is **not** a parallel document-store library and **not**
``harness.memory[]`` attach IDs. It is a thin owner-scoped projection of the
AI Durable Memory Service Tier-2 bank:

* ``tenant``   = ``TenantPrincipal.tenant_id`` (= user.id MVP; service JWT
  claim; never client-asserted alone)
* ``subject``  = authenticated user.id (ownership / privacy boundary)
* ``namespace`` = ``facts`` (product Tier-2 bank partition; optional override)

List/search map to ``POST /v1/memory/recall``; forget/erase map to
``POST /v1/memory/forget`` and ``POST /v1/memory/erase_subject``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


DEFAULT_MEMORY_NAMESPACE = "facts"

MemoryType = Literal["short_term", "long_term", "episodic", "semantic", "procedural"]
MemorySource = Literal["user", "agent", "system", "external"]


class MemoryEntryOut(BaseModel):
    id: str
    content: str
    type: MemoryType = "long_term"
    source: MemorySource = "system"
    session_id: str | None = None
    harness_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float | None = None
    created_at: str
    expires_at: str | None = None


class MemoryListResponse(BaseModel):
    data: list[MemoryEntryOut]
    total: int
    page: int = 1
    page_size: int = 50


class MemoryDetailResponse(BaseModel):
    data: MemoryEntryOut


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    type: MemoryType = "long_term"
    source: MemorySource = "user"
    session_id: str | None = None
    harness_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    namespace: str | None = None
    expires_at: str | None = None


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] | None = None
    expires_at: str | None = None
    namespace: str | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    type: MemoryType | None = None
    session_id: str | None = None
    harness_id: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)
    min_relevance: float | None = None
    namespace: str | None = None


class MemorySearchResponse(BaseModel):
    results: list[MemoryEntryOut]
    query: str
    total_found: int


class MemoryStatsResponse(BaseModel):
    total_entries: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    storage_used_bytes: int = 0


class MemoryForgetRequest(BaseModel):
    """Owner forget — BFF forces scope.subject to the current user id."""

    item_id: str | None = None
    namespace: str | None = None
    all_in_scope: bool = False
    reason: str = "user_request"
    # Optional client hints ignored for tenancy (claim wins).
    scope: dict[str, Any] | None = None
    selector: dict[str, Any] | None = None
    actor: str | None = None


class MemoryEraseSubjectRequest(BaseModel):
    """Owner erase_subject — BFF forces tenant+subject from the principal."""

    reason: str = "user_request"
    scope: dict[str, Any] | None = None
    actor: str | None = None


class MemoryForgetResponse(BaseModel):
    forgotten: int


class MemoryEraseSubjectResponse(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
