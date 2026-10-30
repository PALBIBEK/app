"""Bring the product's database schema to the current version at boot, from many processes.

On Postgres the schema is the Alembic migrations in ``db/migrations``, applied under a
session-level advisory lock: every API replica may start at once, one migrates, the others
wait and then find nothing to do. SQLite (tests) keeps ``create_all``; a test checks that the
models and the migrations describe the same schema, so the two cannot drift.

Before this, development ran ``create_all`` (which never alters a table that exists) and
production created nothing. A database made that way has tables but no migration history:
it is *adopted* by finding the last revision whose changes it already holds (each revision
adds a table or column that marks it) and recording that, then migrating from there.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Inspector, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from prime_agent.db import Base

__all__ = ["alembic_config", "legacy_revision", "migrate"]

logger = logging.getLogger(__name__)

MIGRATIONS: Final = Path(__file__).resolve().parent / "migrations"
_LOCK_KEY: Final = 0x0B4C_E5D0  # pg_advisory_lock key for "product schema"


def _has_column(table: str, column: str) -> Callable[[Inspector], bool]:
    return lambda found: found.has_table(table) and column in {c["name"] for c in found.get_columns(table)}


def _has_table(table: str) -> Callable[[Inspector], bool]:
    return lambda found: found.has_table(table)


_MARKS: Final[tuple[tuple[str, Callable[[Inspector], bool]], ...]] = (
    ("f5c65e7c13a0", _has_table("users")),
    ("a1b2c3d4e5f6", _has_column("harnesses", "bind_status")),
    ("b2c3d4e5f6a7", _has_table("session_links")),
    ("c3d4e5f6a7b8", _has_column("session_links", "scope_id")),
    ("d4e5f6a7b8c9", _has_table("harness_releases")),
    ("e5f6a7b8c9d0", _has_column("harnesses", "published_tools")),
    ("f6a7b8c9d0e1", _has_table("trace_datasets")),
    ("a7b8c9d0e1f2", _has_table("trace_proposals")),
    ("b8c9d0e1f2a3", _has_table("auth_throttle")),
)
"""Each revision up to the first one made after adoption, with what shows it was applied."""


def alembic_config() -> Config:
    """Alembic's configuration for the packaged migrations (no alembic.ini needed)."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def legacy_revision(found: Inspector) -> str | None:
    """The last revision a database without migration history already holds, or None when it
    has no product tables (a new database)."""
    held = None
    for revision, applied in _MARKS:
        if not applied(found):
            break
        held = revision
    return held


def _upgrade(connection: Connection) -> None:
    config = alembic_config()
    config.attributes["connection"] = connection
    untracked = MigrationContext.configure(connection).get_current_revision() is None
    if untracked and (held := legacy_revision(inspect(connection))) is not None:
        logger.warning("adopting a database made without migrations: it holds revision %s", held)
        command.stamp(config, held)
    command.upgrade(config, "head")


async def migrate(engine: AsyncEngine) -> None:
    """Bring the schema to head (Postgres: migrations under a lock; SQLite: ``create_all``)."""
    if engine.dialect.name != "postgresql":
        from prime_agent.db import models  # noqa: F401  (registers every table)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY})
        try:
            await conn.run_sync(_upgrade)
            await conn.commit()
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
            await conn.commit()
    logger.info("database schema is at the latest migration")
