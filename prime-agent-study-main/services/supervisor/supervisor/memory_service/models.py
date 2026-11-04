"""SQLAlchemy models for Tier 1 session log + outbox + leases + Tier 2 bank."""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class MemorySessionRow(Base):
    __tablename__ = "memory_session"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    format_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("memory_session.id"), nullable=True
    )
    forked_at_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SessionEventRow(Base):
    __tablename__ = "session_event"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_session_event_seq"),)

    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_session.id"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class SessionArchiveRow(Base):
    """An archived session's events, compressed (gzip JSONL), in the database every replica
    reads: they used to go to a file on the archiving replica's disk."""

    __tablename__ = "memory_session_archive"

    session_id: Mapped[str] = mapped_column(Text, ForeignKey("memory_session.id"), primary_key=True)
    events: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    through_seq: Mapped[int] = mapped_column(Integer, nullable=False)  # the last seq it holds
    archived_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_outbox_idem"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_session.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    tool_call_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # pending | running | done
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # When an executor claimed it (status running): a claim older than the outbox's
    # CLAIM_TIMEOUT_MS is taken to be a crashed executor's, and may be claimed again.
    claimed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class MemoryItemRow(Base):
    """Tier 2 semantic bank — rebuildable projection of Tier 1."""

    __tablename__ = "memory_item"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_session: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    superseded_by: Mapped[str | None] = mapped_column(
        Text, ForeignKey("memory_item.id"), nullable=True
    )
    valid_from_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    forgotten: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExtractionJobRow(Base):
    """Async extraction queue — never on the request/turn critical path."""

    __tablename__ = "extraction_job"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_session.id"), nullable=False, index=True
    )
    seq_from: Mapped[int] = mapped_column(Integer, nullable=False)
    seq_to: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # pending | running | done | error
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ForgetAuditRow(Base):
    """Who/why audit trail for forget and subject erasure."""

    __tablename__ = "forget_audit"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    item_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="forget")
