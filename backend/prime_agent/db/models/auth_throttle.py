"""Failed sign-in counters, shared by every API replica (``prime_agent.security.throttle``)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base


class AuthThrottle(Base):
    """One counter: ``key`` names a rule and what it counts (hashed: no email or address
    is stored), ``count`` the attempts since ``window_started_at``."""

    __tablename__ = "auth_throttle"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
