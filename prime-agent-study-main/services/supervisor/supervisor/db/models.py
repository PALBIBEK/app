"""SQLAlchemy models for the journal schema."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # Previous sessions of one agent version for one person (memory, phase 7).
        Index("ix_sessions_owner_pin", "tenant_id", "owner_principal_id", "agent_pin"),
    )

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
    # Memory: who the session belongs to, the exact agent version it pins, and which
    # notebooks it reads and writes (see supervisor.memory.binding).
    owner_principal_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_pin: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        # Idempotent appends look events up by their deterministic id.
        Index("ix_events_session_event_id", "session_id", "event_id"),
    )

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
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
    # The tenant that registered it through the API; None for shipped versions.
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogArtifactRow(Base):
    """Persisted composition catalog artifacts (publish/deprecate API).

    Extended with creator/owner tracking for observability.
    """

    __tablename__ = "catalog_artifacts"
    __table_args__ = (
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
    # Who settled it and when: a principal id, or None for a timeout or the sweeper.
    responded_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class WorkspaceFileVersionRow(Base):
    """Every saved state of a workspace path (history, restore). Bytes live in ObjectStorage
    under the version's own opaque key; a restore records a new version pointing at old bytes."""

    __tablename__ = "workspace_file_versions"
    __table_args__ = (
        UniqueConstraint("scope_id", "path", "version", name="uq_workspace_file_versions_path_version"),
        Index("ix_workspace_file_versions_scope_sha", "scope_id", "content_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(36), ForeignKey("scopes.id"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Where the bytes came from when not a person's save: ``run:<session>/<turn>`` for an
    agent's edits, ``artifact:<id>`` for a saved run output."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class SessionAttachmentRow(Base):
    """A workspace file attached to a conversation, pinned to one version (contextual use).
    The session's runs see exactly that version, read-only, in ``.attachments/``."""

    __tablename__ = "session_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    """Its file name in ``.attachments/`` (unique among the session's attachments)."""
    attached_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunInputRow(Base):
    """What one run (turn) started from: each attachment and each workspace file it could
    read, by version and SHA-256. Recorded at the run's start; never changed."""

    __tablename__ = "run_inputs"
    __table_args__ = (Index("ix_run_inputs_session_turn", "session_id", "turn_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    """``attachment`` or ``workspace``."""
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ArtifactRow(Base):
    """A file an agent run produced for the user (from ``.outputs/``), collected and checked
    by the supervisor, never by the agent. The user may save it into a workspace folder."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_session", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    """Its path under ``.outputs/``."""
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    saved_scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    saved_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


class VaultSecretRow(Base):
    """One sealed secret (AES-256-GCM, bound to its owner and name); see ``supervisor.vault``."""

    __tablename__ = "vault_secrets"
    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", "name", name="uq_vault_secrets_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class HandLeaseRow(Base):
    """Who holds a session's hand, if anyone besides its session (``supervisor.hands.leases``).

    ``token`` only grows: each new lease of the hand gets the next one, and a
    holder's calls carry theirs, so a holder whose lease ended (expired, or
    taken back) is refused even if it never learnt that (a fencing token).
    """

    __tablename__ = "hand_leases"

    hand_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    holder: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expires_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class RunnerRow(Base):
    """A tenant's runner: a machine of theirs that hosts hands and dials out to us."""

    __tablename__ = "runners"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RunnerCommandRow(Base):
    """One operation for a runner to carry out (``supervisor.hands.runners``)."""

    __tablename__ = "runner_commands"
    __table_args__ = (Index("ix_runner_commands_queue", "tenant_id", "runner", "status", "created_ms"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runner: Mapped[str] = mapped_column(String(64), nullable=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    op: Mapped[str] = mapped_column(String(32), nullable=False)
    args: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class WorkerHeartbeatRow(Base):
    """A live session worker: refreshed every few seconds, removed on a clean stop
    (``supervisor.temporal.fleet``)."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (Index("ix_worker_heartbeats_queue", "task_queue", "last_seen_ms"),)

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    task_queue: Mapped[str] = mapped_column(String(255), nullable=False)
    home_queue: Mapped[str] = mapped_column(String(255), nullable=False)
    max_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    busy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RunTraceRow(Base):
    """One run's trace rollup (``supervisor.traces``): what lists and filters read, so they never
    scan events. Rebuilt from the journal, which stays the source of truth: written when a run
    starts and ends, refreshed on read while it is unfinished."""

    __tablename__ = "run_traces"
    __table_args__ = (
        Index("ix_run_traces_tenant_started", "tenant_id", "started_ms"),
        Index("ix_run_traces_session_started", "session_id", "started_ms"),
        Index("ix_run_traces_composition", "tenant_id", "composition_id", "composition_digest"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    harness_version: Mapped[str] = mapped_column(String(255), nullable=False)
    composition_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    composition_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    denials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    """Automatic checks this run failed (``supervisor.traces.checks``)."""
    feedback_good: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    feedback_bad: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rollup: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TraceScoreRow(Base):
    """A verdict on a run or one of its spans: an automatic check, or a person's feedback
    (``supervisor.traces.scores``). Kept with the agent version that ran, so quality can be
    compared version by version."""

    __tablename__ = "trace_scores"
    __table_args__ = (
        Index("ix_trace_scores_run", "session_id", "run_id"),
        Index("ix_trace_scores_version", "tenant_id", "composition_id", "composition_digest", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """None: the score is on the whole run."""
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    """``check`` (automatic), ``person`` (feedback), ``judge`` (a model's rubric)."""
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluator: Mapped[str] = mapped_column(String(64), nullable=False)
    """What judged it, versioned (``checks@1``; ``person`` for feedback)."""
    principal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    composition_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    composition_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
