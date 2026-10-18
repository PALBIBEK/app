"""Harness Pydantic schemas — product CRUD + D3 bind view."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prime_agent.db.models.harness import Harness



def normalize_harness_mode(mode: str) -> str:
    """Map legacy chat|completion|agent to sticky|stateless.

    Canonical modes: sticky, stateless.
    Legacy: chat → sticky; completion|agent → stateless.
    """
    value = (mode or "").strip().lower()
    legacy = {
        "chat": "sticky",
        "completion": "stateless",
        "agent": "stateless",
    }
    if value in legacy:
        return legacy[value]
    if value in {"sticky", "stateless"}:
        return value
    raise ValueError(
        f"Invalid mode {mode!r}. Must be sticky|stateless "
        "(legacy chat→sticky, completion|agent→stateless)"
    )

BindStatus = Literal["unbound", "bound", "stale", "error"]
CatalogStatus = Literal["offered", "deprecated", "unknown"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FLOATING = ("latest", ":latest", "@latest")


class HarnessBindView(BaseModel):
    """Nested bind/pin projection on harness responses."""

    model_config = ConfigDict(extra="forbid")

    bind_status: BindStatus = "unbound"
    harness_version: str | None = None
    runtime: str | None = None
    composition_id: str | None = None
    composition_digest: str | None = None
    bound_at: datetime | None = None
    catalog_status: CatalogStatus | None = None


class HarnessBindRequest(BaseModel):
    """Bind a product harness to an offered composition digest."""

    model_config = ConfigDict(extra="forbid")

    composition_id: str | None = Field(
        default=None,
        description="Composition artifact id (default: coding-v1)",
    )
    composition_digest: str | None = Field(
        default=None,
        description="Immutable digest; default resolved from shipped map after live validate",
    )
    runtime: str | None = Field(
        default=None,
        description="Optional runtime override (MVP defaults from shipped map)",
    )

    @field_validator("composition_id", "composition_digest", "runtime")
    @classmethod
    def reject_floating(cls, value: str | None) -> str | None:
        if value is None:
            return value
        lowered = value.strip().lower()
        if any(tag in lowered for tag in _FLOATING):
            raise ValueError("floating refs (latest) are not allowed")
        return value.strip()

    @field_validator("composition_digest")
    @classmethod
    def digest_shape(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _DIGEST_RE.match(value):
            raise ValueError("composition_digest must be sha256:<64 hex>")
        return value


class HarnessBase(BaseModel):
    """Base harness schema with common fields."""

    name: str = Field(..., min_length=1, max_length=255, description="Harness name (unique)")
    description: str | None = Field(None, description="Optional description")
    model: str = Field(..., description="LLM model: claude-sonnet-4, gpt-4o, etc.")
    mode: str = Field(default="chat", description="Execution mode: chat, completion, agent")
    tools: list[str] = Field(default_factory=list, description="List of tool IDs")
    memory: list[str] = Field(default_factory=list, description="List of memory store IDs")
    system_prompt: str | None = Field(None, description="System prompt for the agent")
    temperature: float | None = Field(None, ge=0, le=2, description="Sampling temperature (0-2)")
    max_tokens: int | None = Field(None, gt=0, description="Maximum tokens to generate")


class HarnessCreate(HarnessBase):
    """Schema for creating a harness."""

    model_config = ConfigDict(extra="forbid")


class HarnessUpdate(BaseModel):
    """Schema for updating a harness (all fields optional)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    model: str | None = None
    mode: str | None = None
    tools: list[str] | None = None
    memory: list[str] | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, gt=0)


class HarnessResponse(HarnessBase):
    """Schema for harness response (includes server-generated fields + bind)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None
    bind: HarnessBindView = Field(default_factory=HarnessBindView)

    @classmethod
    def from_harness(cls, harness: Harness) -> HarnessResponse:
        status = getattr(harness, "bind_status", None) or "unbound"
        return cls(
            id=harness.id,
            name=harness.name,
            description=harness.description,
            model=harness.model,
            mode=harness.mode,
            tools=list(harness.tools or []),
            memory=list(harness.memory or []),
            system_prompt=harness.system_prompt,
            temperature=harness.temperature,
            max_tokens=harness.max_tokens,
            created_at=harness.created_at,
            updated_at=harness.updated_at,
            created_by=harness.created_by,
            updated_by=harness.updated_by,
            bind=HarnessBindView(
                bind_status=status,  # type: ignore[arg-type]
                harness_version=getattr(harness, "harness_version", None),
                runtime=getattr(harness, "runtime", None),
                composition_id=getattr(harness, "composition_id", None),
                composition_digest=getattr(harness, "composition_digest", None),
                bound_at=getattr(harness, "bound_at", None),
                catalog_status="offered" if status == "bound" else None,
            ),
        )


class HarnessListResponse(BaseModel):
    """Paginated list response."""

    data: list[HarnessResponse]
    total: int
    page: int
    page_size: int

class RunnablePin(BaseModel):
    """Internal D4 handoff shape — stored pins only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    harness_id: str
    harness_version: str
    runtime: str
    composition_id: str
    composition_digest: str


class CatalogCompositionSummary(BaseModel):
    """Parse-only map of supervisor CatalogArtifactOut."""

    model_config = ConfigDict(extra="ignore")

    kind: str = "composition"
    artifact_id: str
    digest: str
    status: str
    semver: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict) and "artifact_id" not in data and "id" in data:
            data = {**data, "artifact_id": data["id"]}
        return data
