"""Notebook tables, in the memory service's database.

New tables come with ``create_all``; a new column on an existing table needs a migration
(``memory_service.schema.MIGRATIONS``). The link from a fact to its semantic-bank item
is its own table rather than a column on ``memory_item``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
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
    expires_on: Mapped[str | None] = mapped_column(Text, nullable=True)  # YYYY-MM-DD, UTC
    # What a typed observation said of itself (G2, ``memory_service.observations``); None on
    # a fact captured as plain text (an explicit capture, or one from before G2).
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)  # user, feedback, project, reference
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class InboxRow(Base):
    """One automatic capture's observations, waiting to become facts (G2, hot spot S1 of
    ``docs/memory-gaps.md``).

    A capture appends here and takes no lock on its notebook, so captures into one notebook
    from many sessions never wait on each other. The inbox's drainer
    (``notebooks.inbox``) promotes a notebook's rows in a batch, under that notebook's lock
    once per batch, marking them promoted in the same transaction. A promoted row is kept
    (``KEEP_PROMOTED_MS``) so a retried burst still finds its idempotency key, then pruned;
    what it held is emptied once it is handled (``store.mark_handled``), so the captured words
    live only in the notebook, under its history and erasure. One set aside keeps them.
    """

    __tablename__ = "memory_observation_inbox"
    __table_args__ = (
        # A retried capture (the same burst) is one row, whichever replica it reached.
        UniqueConstraint("notebook_id", "idempotency_key", name="uq_memory_inbox_key"),
        # What the drainer reads every pass: the queue, never the day of promoted rows kept.
        Index(
            "ix_memory_inbox_queued",
            "notebook_id",
            "id",
            postgresql_where=text("promoted_at IS NULL"),
            sqlite_where=text("promoted_at IS NULL"),
        ),
        Index("ix_memory_inbox_promoted", "promoted_at"),  # pruning
        # The few set aside (and failing), counted on every metrics scrape.
        Index(
            "ix_memory_inbox_attempts",
            "attempts",
            postgresql_where=text("attempts > 0"),
            sqlite_where=text("attempts > 0"),
        ),
    )

    id: Mapped[int] = mapped_column(_Id, primary_key=True, autoincrement=True)
    notebook_id: Mapped[str] = mapped_column(
        Text, ForeignKey("memory_notebooks.id"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[list] = mapped_column(JSON, nullable=False)  # wire.observation_out each
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # len(observations): counted, not parsed
    retire: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # fact ids it makes false
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_on: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    promoted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # failures landing on its own
    failed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # its last counted failure
