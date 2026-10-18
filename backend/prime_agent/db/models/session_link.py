"""Product↔AI session ownership index (D4)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base


class SessionLink(Base):
    """Maps a product user to a supervisor session with immutable pin snapshot."""

    __tablename__ = "session_links"
    __table_args__ = (
        UniqueConstraint("ai_session_id", name="uq_session_links_ai_session_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    harness_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("harnesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ai_session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Immutable pin snapshot at create time
    harness_version: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    composition_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="active",
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
