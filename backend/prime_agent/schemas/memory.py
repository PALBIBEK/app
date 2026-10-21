"""Long-term memory: a user's notebooks for one harness, as the supervisor serves them.

The shapes mirror the supervisor's notebook API (``/v1/memory``); the backend adds
only the harness, from which it derives the agent version the notebooks belong to.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NotebookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)


class NotebookArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived: bool


class NotebookResponse(BaseModel):
    id: str
    agent_pin: str
    name: str
    label: str | None = None
    revision: int
    created_at: int
    archived: bool
    facts: int
    bytes: int
    updated_at: int | None = None


class FactResponse(BaseModel):
    id: str
    text: str
    sensitivity: str
    sources: list[str]
    source_unknown: bool
    author: str | None = None
    created_at: int
    updated_at: int


class FactsAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[str] = Field(min_length=1, max_length=100)


class FactEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = Field(min_length=1, max_length=4000)


class FactsEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edits: list[FactEdit] = Field(min_length=1, max_length=100)


class FactsForgetRequest(BaseModel):
    """Forget these facts (the notebook's history keeps them for restore)."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=500)


class FactsAddedResponse(BaseModel):
    added: int


class FactsRemovedResponse(BaseModel):
    removed: int


class RevisionResponse(BaseModel):
    revision: int
    fact_id: str
    op: str
    text: str
    sensitivity: str
    at: int
    author: str | None = None


class NotebookRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)


class MemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    notebook_id: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MemoryHit(BaseModel):
    notebook_id: str
    fact: str
