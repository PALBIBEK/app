"""BFF response schema for AI health proxy — never includes service tokens."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AiHealthResponse(BaseModel):
    """Sanitized upstream health view for the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    upstream: Literal["supervisor"] = "supervisor"
    status: str
    tenant_id: str
    principal_id: str
    detail: dict[str, Any] = Field(default_factory=dict)
