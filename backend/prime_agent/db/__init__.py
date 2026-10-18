"""Database package — declarative Base for all ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for Prime Agent models."""


__all__ = ["Base"]
