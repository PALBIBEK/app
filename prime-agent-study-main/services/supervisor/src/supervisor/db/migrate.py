"""Bring the database schema to the current version at boot, safely from many processes.

On Postgres the schema is the Alembic migrations in ``db/migrations``, applied
under a session-level advisory lock: the API and every worker may start at
once, one migrates, the others wait and then find nothing to do. (Before, each
ran ``create_all`` and the losers of the race crashed on duplicate types, and
``create_all`` never altered a table that already existed.)

SQLite (development and tests, always a fresh file or memory) keeps
``create_all``; CI checks that the models and the migrations describe the same
schema, so the two cannot drift.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from supervisor.db.engine import create_all

__all__ = ["LegacySchemaError", "alembic_config", "migrate"]

logger = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_LOCK_KEY = 0x5E5_0C1A  # pg_advisory_lock key for "supervisor schema"


class LegacySchemaError(RuntimeError):
    """The database has tables but no migration history (made by a build without migrations)."""


def alembic_config() -> Config:
    """Alembic's configuration for the packaged migrations (no alembic.ini needed)."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["configure_logger"] = False
    return config


def _upgrade(connection: Connection) -> None:
    if MigrationContext.configure(connection).get_current_revision() is None and inspect(
        connection
    ).has_table("sessions"):
        raise LegacySchemaError(
            "the database has supervisor tables but no migration history: it was created by "
            "a build without migrations. Recreate it, or, if its schema is known to be "
            "current, record that with `alembic stamp head`."
        )
    config = alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def migrate(engine: AsyncEngine) -> None:
    """Bring the schema to head (Postgres: migrations under a lock; SQLite: create_all)."""
    if engine.dialect.name != "postgresql":
        await create_all(engine)
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
