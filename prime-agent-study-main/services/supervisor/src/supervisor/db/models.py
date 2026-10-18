"""SQLAlchemy models for the journal schema."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    # WP-M4: control-plane scope binding (nullable until session opts into workspace).
    scope_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scopes.id"), nullable=True, index=True
    )
    runtime: Mapped[str] = mapped_column(Text, nullable=False, default="echo")
    harness_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    composition_pins: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),)

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class SessionIndexRow(Base):
    __tablename__ = "session_index"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), primary_key=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandRow(Base):
    """HTTP command_id dedup — survives process restart. API response only."""

    __tablename__ = "commands"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), primary_key=True
    )
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class HarnessVersionRow(Base):
    """Published harness versions — immutable manifest body once active."""

    __tablename__ = "harness_versions"

    harness_version: Mapped[str] = mapped_column(Text, primary_key=True)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CatalogArtifactRow(Base):
    """Persisted composition catalog artifacts (publish/deprecate API).

    Extended with creator/owner tracking for observability.
    """

    __tablename__ = "catalog_artifacts"
    __table_args__ = (
        UniqueConstraint("kind", "artifact_id", "digest", name="uq_catalog_artifact"),
        Index("ix_catalog_artifacts_created_by", "created_by"),
        Index("ix_catalog_artifacts_owner_id", "owner_id"),
        Index("ix_catalog_artifacts_status", "status"),
    )

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    digest: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="offered")
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Creator/owner tracking (new columns)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    # Indexed via __table_args__ (ix_catalog_artifacts_owner_id); index=True here would duplicate it.
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    deprecated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ApprovalRow(Base):
    """Persisted human-in-the-loop approvals — survive process restart."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    choice: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_s: Mapped[float] = mapped_column(Numeric(18, 3), nullable=False, default=300)
    harness_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolOutboxRow(Base):
    """Supervisor tool-invoke outbox — exactly-once completion for invoke_tool."""

    __tablename__ = "tool_outbox"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_tool_outbox_idem"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ScopeRow(Base):
    """Workspace scope (personal / project / channel) — Track B WP-M2."""

    __tablename__ = "scopes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False, default="personal")
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WorkspaceFileRow(Base):
    """Durable workspace file metadata — bytes live in ObjectStorage."""

    __tablename__ = "workspace_files"
    __table_args__ = (
        Index(
            "uq_workspace_files_scope_path_live",
            "scope_id",
            "path",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scopes.id"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScopeMembershipRow(Base):
    """Principal membership in a workspace scope (Track B WP-M4)."""

    __tablename__ = "scope_memberships"
    __table_args__ = (
        UniqueConstraint(
            "scope_id", "principal_id", name="uq_scope_memberships_scope_principal"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scopes.id"), nullable=False, index=True
    )
    principal_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ScopeGrantRow(Base):
    """Explicit grant row for a principal on a scope (Track B WP-M4)."""

    __tablename__ = "scope_grants"
    __table_args__ = (
        UniqueConstraint(
            "scope_id",
            "principal_id",
            "grant",
            name="uq_scope_grants_scope_principal_grant",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scopes.id"), nullable=False, index=True
    )
    principal_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    grant: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SandboxRouteRow(Base):
    """Dynamic sandbox routing — maps scopes to backend providers.

    Enables per-scope backend selection with migration tracking.
    Reference: QM's sandbox-routing.ts pattern.
    """

    __tablename__ = "sandbox_routes"

    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    backend: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    image_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    migrated_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    migration_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities_lost: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CatalogUsageRow(Base):
    """Catalog artifact usage records for analytics.

    Tracks all artifact resolution, binding, and session start events.
    Supports usage analytics dashboards and popular artifacts queries.
    """

    __tablename__ = "catalog_usage"
    __table_args__ = (
        Index("ix_catalog_usage_artifact", "kind", "artifact_id", "digest"),
        Index("ix_catalog_usage_session", "session_id"),
        Index("ix_catalog_usage_principal", "principal_id"),
        Index("ix_catalog_usage_timestamp", "timestamp_ms"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)  # resolved, bound, session_started
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CatalogErrorRow(Base):
    """Catalog error records for analytics.

    Tracks artifact-related errors for error rate calculation
    and debugging analytics.
    """

    __tablename__ = "catalog_errors"
    __table_args__ = (
        Index("ix_catalog_errors_artifact", "kind", "artifact_id", "digest"),
        Index("ix_catalog_errors_code", "error_code"),
        Index("ix_catalog_errors_timestamp", "timestamp_ms"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str] = mapped_column(Text, nullable=False)
    principal_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
