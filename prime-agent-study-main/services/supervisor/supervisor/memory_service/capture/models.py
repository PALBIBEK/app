"""The durable capture queue's tables (G3), in the memory service's database.

Grok keeps one queue per scope in a SQLite file (``xai-grok-memory/src/v2_capture.rs::
V2CaptureStore``). Ours holds every tenant's sessions in these tables, shared by every
replica and worker: a session's cursors, its jobs (one per burst of turns), and each
notebook's count of committed captures.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from supervisor.memory_service.models import Base


class CaptureSessionRow(Base):
    """A session's cursors, in turns: every turn through ``requested`` is queued, through
    ``captured`` its outcome is committed (or failed for good), and through ``indexed`` its
    facts have landed in the notebook (G2's inbox), so recall finds them. Each only grows."""

    __tablename__ = "memory_capture_sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CaptureJobRow(Base):
    """One burst of a session's turns to capture.

    ``status`` is ``pending`` (never claimed), ``running`` (leased), ``completed`` (its
    outcome committed) or ``failed`` (``terminal``: for good; otherwise retried once
    ``available_at`` passes). ``attempt`` counts claims and is the lease's generation: every
    write a lease holder makes names it, so a worker whose lease was taken over is fenced
    out. ``outcome`` is kept once committed, so landing it again after a crash (``queued``
    not yet set) never extracts again.
    """

    __tablename__ = "memory_capture_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "from_turn", "through_turn", name="uq_memory_capture_range"),
        # Claims read only work that may be claimed: never the week of completed jobs kept.
        Index(
            "ix_memory_capture_claimable",
            "tenant_id",
            "available_at",
            postgresql_where=text("status <> 'completed' AND NOT terminal"),
            sqlite_where=text("status <> 'completed' AND NOT terminal"),
        ),
        # Reconciliation reads only committed jobs whose facts have not landed yet.
        Index(
            "ix_memory_capture_unindexed",
            "completed_at",
            postgresql_where=text("status = 'completed' AND NOT indexed"),
            sqlite_where=text("status = 'completed' AND NOT indexed"),
        ),
        # A notebook's committed outcomes not queued yet: what every resolution reads (S3).
        Index(
            "ix_memory_capture_unqueued",
            "notebook_id",
            postgresql_where=text("status = 'completed' AND NOT queued"),
            sqlite_where=text("status = 'completed' AND NOT queued"),
        ),
        Index("ix_memory_capture_session", "session_id", "from_turn"),
        Index("ix_memory_capture_updated", "updated_at"),  # pruning
    )

    job_id: Mapped[str] = mapped_column(Text, primary_key=True)  # from (session, its run ids)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    notebook_id: Mapped[str] = mapped_column(Text, nullable=False)  # where its facts go
    run_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    from_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    through_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Claims that ended badly (failed, or the holder's lease ran out): what gives up on a job
    # at MAX_CAPTURE_ATTEMPTS. A release (a worker stopping, a resolution gone stale) is not one.
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Releases because the notebook kept changing under every resolution (S3): each waits
    # longer, and past MAX_STALE_RELEASES the next commit is not held to its revision.
    stale: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available_at: Mapped[int] = mapped_column(BigInteger, nullable=False)  # a retry's backoff
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # sanitized, bounded
    outcome_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # None is SQL NULL
    queued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # in the inbox
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # landed
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CaptureNotebookRow(Base):
    """A notebook's committed captures, counted: part of the revision a capture's resolution
    reads (S3). Every commit that changes the notebook takes this row's lock and counts
    itself, so of two captures resolved against one revision, the second to commit finds it
    changed, even before the first's facts are queued."""

    __tablename__ = "memory_capture_notebooks"

    notebook_id: Mapped[str] = mapped_column(Text, primary_key=True)
    commits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
