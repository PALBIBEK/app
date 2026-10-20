"""Notebook tables, in the memory service's database.

New tables only, so ``create_all`` brings an existing memory database up to date
(the service has no migrations): the link from a fact to its semantic-bank item
is its own table rather than a column on ``memory_item``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from supervisor.memory_service.models import Base

# SQLite only autoincrements INTEGER PRIMARY KEY; Postgres gets a 64-bit identity.
_Id = BigInteger().with_variant(Integer, "sqlite")


class NotebookRow(Base):
    """One named notebook. ``revision`` is bumped by every changing transaction and
    doubles as the notebook's lock row."""

    __tablename__ = "memory_notebooks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "owner_principal_id", "agent_pin", "name", name="uq_memory_notebook_name"
        ),
        Index("ix_memory_notebooks_owner_pin", "tenant_id", "owner_principal_id", "agent_pin"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # <tenant>~<hex> (types.notebook_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_pin: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The agent's name (survives new versions): the bank namespace of its facts."""
    name: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_tidied_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FactRow(Base):
    """A fact's current state; ``deleted_at`` set means removed (history keeps it)."""

    __tablename__ = "memory_facts"
    __table_args__ = (Index("ix_memory_facts_notebook_live", "notebook_id", "deleted_at", "id"),)

    id: Mapped[int] = mapped_column(_Id, primary_key=True, autoincrement=True)
    notebook_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_notebooks.id"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FactRevisionRow(Base):
    """Append-only: every insert, update, delete or restore of a fact, in order."""

    __tablename__ = "memory_fact_revisions"
    __table_args__ = (Index("ix_memory_revisions_notebook", "notebook_id", "revision"),)

    id: Mapped[int] = mapped_column(_Id, primary_key=True, autoincrement=True)
    notebook_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_notebooks.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    op: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class FactItemRow(Base):
    """A live fact's item in the semantic bank (one per fact; replaced when it changes)."""

    __tablename__ = "memory_fact_items"

    fact_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    notebook_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_notebooks.id"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(Text, ForeignKey("memory_item.id"), nullable=False)
