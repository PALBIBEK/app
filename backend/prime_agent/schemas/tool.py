"""Tool Pydantic schemas — aligned with FE tool.types.ts / D6."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolParameterSchema(BaseModel):
    """Single tool parameter definition."""

    name: str
    type: str = Field(description="string | number | boolean | array | object")
    description: str = ""
    required: bool = False
    default: Any | None = None


class ToolBase(BaseModel):
    """Shared tool fields."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    category: str = Field(
        ...,
        description="Category: search, data, code, communication, system, ai, custom",
    )
    status: str = Field(default="active")
    parameters: list[ToolParameterSchema] = Field(default_factory=list)
    endpoint: str | None = None
    auth_required: bool = False
    rate_limit: int | None = Field(default=None, gt=0)


class ToolCreate(ToolBase):
    model_config = ConfigDict(extra="forbid")


class ToolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = None
    status: str | None = None
    parameters: list[ToolParameterSchema] | None = None
    endpoint: str | None = None
    auth_required: bool | None = None
    rate_limit: int | None = Field(default=None, gt=0)


class ToolResponse(ToolBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None


class ToolListResponse(BaseModel):
    """Paginated list response."""

    data: list[ToolResponse]
    total: int
    page: int
    page_size: int


class ToolExecuteRequest(BaseModel):
    """Lab execute body — owner QA outside session (not a journal substitute)."""

    model_config = ConfigDict(extra="forbid")

    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    """Structured lab execute outcome (aligned with FE ToolExecutionResult)."""

    tool_id: str
    execution_id: str
    status: str = Field(description="success | error | timeout")
    result: Any | None = None
    error: str | None = None
    duration_ms: float
    timestamp: datetime


class ToolExecuteResponse(BaseModel):
    """Lab execute envelope matching FE toolsAPI.execute."""

    execution_id: str
    result: ToolExecutionResult
