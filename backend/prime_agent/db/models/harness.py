"""
Harness model - AI agent configuration.

Following corridor-platforms pattern with:
- UUID primary key
- AuditableMixin for audit trail
- JSONB for array fields (tools, memory)
- Matching frontend TypeScript types
"""
from uuid import uuid4

from datetime import datetime

from sqlalchemy import DateTime, JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base
from prime_agent.db.models.mixins import AuditableMixin

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Harness(Base, AuditableMixin):
    """
    Harness - AI agent configuration.

    Represents a configured AI agent with specific model, tools, and settings.

    Attributes:
        id: UUID primary key
        name: Harness name (required, unique)
        description: Optional description
        model: LLM model (claude-sonnet-4, gpt-4o, etc.)
        mode: Execution mode (chat, completion, agent)
        tools: List of tool IDs enabled for this harness
        memory: List of memory store IDs
        system_prompt: System prompt for the agent
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens to generate
    """

    __tablename__ = "harnesses"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_harnesses_user_id_name"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="UUID primary key",
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User that owns this harness",
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Harness name (unique)",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description",
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="LLM model: claude-sonnet-4, claude-opus-4, gpt-4-turbo, gpt-4o",
    )

    mode: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="chat",
        server_default=text("'chat'"),
        comment="Execution mode: chat, completion, agent",
    )

    tools: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=list,
        server_default="[]",
        comment="List of tool IDs (stored as JSON array)",
    )

    memory: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=list,
        server_default="[]",
        comment="List of memory store IDs (stored as JSON array)",
    )

    system_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="System prompt for the agent",
    )

    temperature: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Sampling temperature (0-2)",
    )

    max_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum tokens to generate",
    )

    # D3 pin — product harness → AI composition (nullable until bind)
    bind_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unbound",
        server_default=text("'unbound'"),
        comment="unbound | bound | stale | error",
    )
    harness_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="AI harness_version pin (e.g. githi-v1)",
    )
    runtime: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="AI runtime pin (e.g. harness | echo)",
    )
    composition_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Composition artifact id",
    )
    composition_digest: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Immutable composition digest sha256:…",
    )
    bound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the harness was last bound",
    )
    bound_by: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="User id that performed the bind",
    )

    def __repr__(self) -> str:
        return f"<Harness id={self.id!r} name={self.name!r} model={self.model!r}>"
