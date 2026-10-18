"""Tool ORM model — product tool catalog entry."""

from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base
from prime_agent.db.models.mixins import AuditableMixin

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Tool(Base, AuditableMixin):
    """Owner-scoped custom/builtin tool definition."""

    __tablename__ = "tools"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_tools_user_id_name"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", server_default=text("'active'")
    )
    parameters: Mapped[list] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default="[]"
    )
    endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    auth_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Tool id={self.id!r} name={self.name!r} category={self.category!r}>"
