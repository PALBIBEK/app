"""User ORM model."""

from uuid import uuid4

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base
from prime_agent.db.models.mixins import AuditableMixin


class User(Base, AuditableMixin):
    """Application user (product control-plane identity)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"
