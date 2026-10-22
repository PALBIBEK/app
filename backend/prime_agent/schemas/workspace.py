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
    """``ok``: the session's sandbox now holds the workspace. ``no_sandbox``: it has none yet;
    it will load the workspace when the agent first needs it, so there is nothing to refresh."""


class WorkspaceUploadStartIn(BaseModel):
    """What the browser will send (it computes the SHA-256 itself)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    mime_type: str | None = Field(default=None, max_length=255)
    expected_version: int | None = Field(default=None, ge=0)


class WorkspaceUploadStartOut(BaseModel):
    """``upload``: PUT the file to ``url`` with ``headers``, then complete with ``upload_token``.
    ``committed``: already stored, ``file`` is recorded. ``proxy``: send it through
    ``PUT /workspace/scopes/{scope}/files/{path}`` instead (storage without upload URLs)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    max_bytes: int
    upload_token: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    expires_in_s: int | None = None
    file: WorkspaceFileOut | None = None


class FileVersionOut(BaseModel):
    """One saved version of a workspace file (newest first in a history)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    content_sha256: str
    size_bytes: int
    mime_type: str | None = None
    created_at: str
    author: str | None = None
    author_email: str | None = None
    restored_from: int | None = None
    source: str | None = None
    current: bool = False


class RestoreVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=0)


class AttachIn(BaseModel):
    """Attach a workspace file to the conversation, pinned to a version (default: current)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    version: int | None = Field(default=None, ge=1)


class AttachmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scope_id: str
    path: str
    version: int
    content_sha256: str
    size_bytes: int
    mime_type: str | None = None
    name: str
    sandbox_path: str
    attached_by: str | None = None
    created_at: str


class ArtifactOut(BaseModel):
    """A file a run made for the user (from its ``.outputs/`` folder)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    turn_id: str
    name: str
    content_sha256: str
    size_bytes: int
    mime_type: str | None = None
    created_at: str
    saved_scope_id: str | None = None
    saved_path: str | None = None
    saved_version: int | None = None


class SaveArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    expected_version: int | None = Field(default=None, ge=0)


class SavedArtifactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactOut
    path: str
    version: int
    scope_id: str


class RunInputOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    scope_id: str
    path: str
    version: int
    content_sha256: str


class WorkspaceUploadCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_token: str = Field(min_length=1, max_length=4096)


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
