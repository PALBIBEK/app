"""Agent workspace file schemas (D9) — durable SoT via supervisor, not sandbox FS.

Distinct from tenant Settings / workspaceStore org chrome.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkspaceFileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content_sha256: str
    object_key: str
    size_bytes: int
    version: int
    mime_type: str | None = None
    file_id: str


class EnsureScopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_id: str
    status: str = "ok"


class WorkspaceRehydrateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"


class WorkspaceUploadMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime_type: str | None = Field(default=None)
    expected_version: int | None = Field(default=None)


class ScopeMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    grants: list[str]
    email: str | None = None  # filled in for members who are users of this product
    name: str | None = None


class InviteScopeMemberIn(BaseModel):
    """Who to share with: a user's email (what a person knows) or their principal id."""

    model_config = ConfigDict(extra="forbid")

    principal_id: str | None = None
    email: str | None = None
    grants: list[str] | None = None

    @model_validator(mode="after")
    def one_of(self) -> InviteScopeMemberIn:
        if bool(self.principal_id) == bool(self.email):
            raise ValueError("give either email or principal_id")
        return self
