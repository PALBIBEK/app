"""Environments: named sandbox setups that several harnesses share."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from prime_agent.schemas.harness_manifest import SandboxSpec


class EnvironmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    sandbox: SandboxSpec


class EnvironmentUser(BaseModel):
    """A harness that uses the environment."""

    id: str
    name: str
    published: bool
    """Its latest release already runs the environment's current settings."""


class EnvironmentView(BaseModel):
    id: str
    name: str
    description: str
    sandbox: SandboxSpec
    used_by: list[EnvironmentUser] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
