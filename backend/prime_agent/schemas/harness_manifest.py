"""A harness manifest: everything a user decides about an agent, in one document.

The builder edits it as a draft; publishing turns it into the supervisor's catalog
artifacts (prompt pack, tool set, sandbox and memory profiles, composition) and a
harness version for the policy. The fields and their allowed values are the
supervisor's own (``catalog/core/types.py``, ``harness/storage/converters.py``), so
a draft that validates here publishes unless something live refuses it (a model the
gateway does not serve, a tool it cannot run).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SECTION_ID = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class PromptSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SECTION_ID)
    text: str = Field(min_length=1, max_length=20_000)


class PromptSpec(BaseModel):
    """The system prompt, in ordered sections. ``{cwd}`` becomes the working directory."""

    model_config = ConfigDict(extra="forbid")

    persona: str = Field(default="assistant", min_length=1, max_length=64)
    sections: list[PromptSection] = Field(min_length=1, max_length=20)

    @field_validator("sections")
    @classmethod
    def unique_ids(cls, sections: list[PromptSection]) -> list[PromptSection]:
        ids = [s.id for s in sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section ids must be unique")
        return sections


class SandboxSpec(BaseModel):
    """Where the agent's commands run and what they may touch, and when the sandbox lives."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["none", "local_process", "local_docker", "e2b"] = "local_docker"
    file_effects: Literal["read_only", "workspace_write", "danger_full_access"] = "workspace_write"
    approval_default: Literal["ask", "never"] = "ask"
    start: Literal["with_run", "on_first_tool"] | None = None
    """``with_run``: built as each run begins, alongside the first model call, so the first
    tool does not wait. ``on_first_tool``: only when a tool needs it. None: the platform's."""
    idle_stop_s: int | None = Field(default=None, ge=0, le=86_400)
    """Stop it after this long unused (its files saved; the next tool call restores them);
    0 never. None: the platform's (15 minutes)."""


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compaction: bool = True
    """Summarize older turns when the context fills, instead of failing.

    Long-term memory (the user's notebooks) is not set here: every session reads and
    writes its user's default notebook unless the session asks otherwise.
    """


class PolicySpec(BaseModel):
    """Limits and approvals: the harness version's loop, budget, guards and approvals."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=30, ge=1, le=500)
    max_cost_per_session: float = Field(default=5.0, gt=0, le=10_000)
    max_tokens_per_turn: int = Field(default=200_000, ge=1_000, le=10_000_000)
    require_approval: list[str] = Field(default_factory=list)
    approval_timeout_s: float = Field(default=300, ge=10, le=86_400)
    on_approval_timeout: Literal["deny", "allow"] = "deny"
    repetition: Literal["abort", "warn", "off"] = "abort"
    oscillation: Literal["abort", "warn", "off"] = "abort"
    stall_iterations: int = Field(default=10, ge=1, le=1_000)


class HarnessManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: PromptSpec
    tools: list[str] = Field(default_factory=list, max_length=64)
    """Names from the supervisor's tool registry (``GET /v1/tools``)."""
    model: str = Field(min_length=1, max_length=200)
    """A model id from ``GET /v1/models`` (``gateway/<route>`` or a native model id)."""
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)
    environment_id: str | None = Field(default=None, max_length=36)
    """A shared environment (``/environments``): when set, its settings are the sandbox (the
    ``sandbox`` field shows them; publishing copies them into the release)."""
    memory: MemorySpec = Field(default_factory=MemorySpec)
    policy: PolicySpec = Field(default_factory=PolicySpec)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, tools: list[str]) -> list[str]:
        return list(dict.fromkeys(tools))

    @model_validator(mode="after")
    def coherent(self) -> HarnessManifest:
        if missing := sorted(set(self.policy.require_approval) - set(self.tools)):
            raise ValueError(f"policy.require_approval names tools the harness does not have: {', '.join(missing)}")
        if self.sandbox.provider == "none" and self.tools:
            needs = {"bash", "edit", "write", "read", "ls", "grep", "git_commit"} & set(self.tools)
            if needs:
                raise ValueError(f"sandbox.provider 'none' cannot run workspace tools: {', '.join(sorted(needs))}")
        return self


def manifest_body(manifest: HarnessManifest) -> dict:
    """The manifest as stored and compared: unset optional fields are left out, so adding
    one never makes an existing release look changed."""
    return manifest.model_dump(mode="json", exclude_none=True)


def default_manifest(sandbox: SandboxSpec | None = None) -> HarnessManifest:
    """A new harness starts as the shipped coding agent, so a first publish just works."""
    return HarnessManifest(
        prompt=PromptSpec(
            persona="coder",
            sections=[
                PromptSection(id="intro", text="You are a coding agent working in {cwd}."),
                PromptSection(id="rules", text="Prefer small diffs. Run the tests before you finish."),
            ],
        ),
        tools=["read", "grep", "ls", "write", "edit", "bash"],
        model="gateway/echo",
        policy=PolicySpec(require_approval=["bash"]),
        **({"sandbox": sandbox} if sandbox is not None else {}),
    )


class HarnessManifestView(BaseModel):
    manifest: HarnessManifest
    published_version: int | None = None
    unpublished_changes: bool


class HarnessReleaseView(BaseModel):
    version: int
    manifest: HarnessManifest
    composition_id: str
    composition_digest: str
    harness_version: str
    runtime: str
    created_at: datetime


class HarnessPublishResponse(BaseModel):
    release: HarnessReleaseView
    created: bool
    """False when the draft matched the latest release: nothing was published."""
