"""The memory database's schema: created, migrated and checked at startup.

``metadata.create_all`` makes the tables a database does not have yet, but it never
changes a table that exists: a column added to a model reached only new databases, and
an existing deployment started fine, then failed on its first query of that column.

Now startup, under a lock (Postgres: an advisory lock, so replicas starting together
take turns):

1. creates the tables that are missing (a new database gets the whole schema);
2. runs each migration in ``MIGRATIONS`` not yet recorded in ``memory_schema_version``,
   in order, and records it (a migration is idempotent: on a new database its change
   is already there);
3. compares every model column with the database and refuses to start, naming what
   is missing, rather than failing later on a query.

A change to a model's columns ships with a migration appended here.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

from sqlalchemy import BigInteger, Connection, Integer, Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column

from supervisor.memory_service.models import Base

__all__ = ["MIGRATIONS", "SchemaOutOfDate", "add_column", "migrate"]

_LOCK_KEY: Final = 0x5E5_0A3E  # pg_advisory_lock key: "memory schema"


class SchemaVersionRow(Base):
    __tablename__ = "memory_schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SchemaOutOfDate(RuntimeError):
    """The database lacks columns the models have: a migration is missing."""


def add_column(table: str, column: str, ddl_type: str) -> Callable[[Connection], None]:
    """A migration adding ``column`` (nullable) to ``table`` unless it is there already."""

    def apply(conn: Connection) -> None:
        if column not in {c["name"] for c in inspect(conn).get_columns(table)}:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'))

    return apply


def _all(*steps: Callable[[Connection], None]) -> Callable[[Connection], None]:
    def apply(conn: Connection) -> None:
        for step in steps:
            step(conn)

    return apply


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "baseline: the schema as created by metadata.create_all", lambda conn: None),
    (2, "outbox.claimed_at: an executor claims an item before running it", add_column("outbox", "claimed_at", "BIGINT")),
    (
        3,
        "extraction_job.claimed_at_ms: a processor claims a job before running it",
        add_column("extraction_job", "claimed_at_ms", "BIGINT"),
    ),
    (
        4,
        "extraction_job.attempts, retry_at_ms: a failed job is retried with backoff",
        _all(
            add_column("extraction_job", "attempts", "INTEGER NOT NULL DEFAULT 0"),
            add_column("extraction_job", "retry_at_ms", "BIGINT"),
        ),
    ),
]
"""(version, what it does, apply). Append only; never renumber or edit a shipped one."""


async def migrate(engine: AsyncEngine) -> None:
    """Bring the memory database to the models' schema, or refuse (``SchemaOutOfDate``)."""
    import supervisor.memory_service.notebooks.models  # noqa: F401 — their tables join Base

    async with engine.connect() as conn:
        postgres = engine.dialect.name == "postgresql"
        if postgres:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
            await conn.commit()
        try:
            await conn.run_sync(_upgrade)
            await conn.commit()
        finally:
            if postgres:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                await conn.commit()


def _upgrade(conn: Connection) -> None:
    Base.metadata.create_all(conn)
    done = {int(v) for (v,) in conn.execute(text("SELECT version FROM memory_schema_version"))}
    for version, name, apply in MIGRATIONS:
        if version in done:
            continue
        apply(conn)
        conn.execute(
            text("INSERT INTO memory_schema_version (version, name, applied_at) VALUES (:v, :n, :t)"),
            {"v": version, "n": name, "t": int(time.time() * 1000)},
        )
    missing = _missing_columns(conn)
    if missing:
        raise SchemaOutOfDate(
            "the memory database lacks columns its models need: "
            + ", ".join(missing)
            + " (add a migration to supervisor.memory_service.schema.MIGRATIONS)"
        )


def _missing_columns(conn: Connection) -> list[str]:
    inspector = inspect(conn)
    missing = []
    for table in Base.metadata.sorted_tables:
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    return missing
