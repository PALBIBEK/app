"""An environment: a named sandbox setup that several harnesses share.

Claude Managed Agents keeps the sandbox definition (an *environment*) apart from the agent;
a session pairs one of each. Here a harness's manifest may name an environment instead of
spelling out its sandbox; publishing copies the environment's settings into the release, so
a published version never changes under its sessions.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base
from prime_agent.db.models.harness_release import JSON_DOCUMENT


def _now() -> datetime:
    return datetime.now(UTC)


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_environments_user_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    spec: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    """A ``SandboxSpec``: provider, file effects, approvals, when it starts, when it stops."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
