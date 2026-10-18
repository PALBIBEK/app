"""Platform status DTOs — no secrets, hosts, or catalog problem dumps."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    status: str
    reason: str | None = None


class PlatformStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    run_allowed: bool
    checked_at: datetime
    components: dict[str, ComponentStatus] = Field(default_factory=dict)
    residual: list[str] = Field(default_factory=list)
