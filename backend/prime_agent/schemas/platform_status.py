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


class WorkerAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["no_workers", "backlog", "at_capacity"]
    message: str


class WorkersStatus(BaseModel):
    """The agent worker fleet, as counts only: ``ok``, ``degraded`` (work waits) or ``down``
    (no worker: sessions and messages wait until one starts; nothing is lost)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "down"]
    polling: int
    slots_busy: int
    slots_max: int
    backlog: int
    backlog_age_s: float
    alerts: list[WorkerAlert] = Field(default_factory=list)


class PlatformStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    run_allowed: bool
    checked_at: datetime
    components: dict[str, ComponentStatus] = Field(default_factory=dict)
    residual: list[str] = Field(default_factory=list)
    # None when the supervisor does not report its workers (older version, or unreachable).
    workers: WorkersStatus | None = None
