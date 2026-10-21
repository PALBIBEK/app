"""A harness's draft manifest and its published releases.

Their own tables (not columns on ``harnesses``) so an existing database gains them
from ``create_all`` without a migration run.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _now() -> datetime:
    return datetime.now(UTC)


class HarnessDraft(Base):
    """What the builder is editing: a manifest not yet published."""

    __tablename__ = "harness_drafts"

    harness_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), primary_key=True
    )
    manifest: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class HarnessRelease(Base):
    """One published version: the manifest as it was, and the composition it became."""

    __tablename__ = "harness_releases"
    __table_args__ = (UniqueConstraint("harness_id", "version", name="uq_harness_releases_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    harness_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    composition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    composition_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    harness_version: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
