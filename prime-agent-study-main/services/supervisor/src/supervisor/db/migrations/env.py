"""Alembic environment for the supervisor's schema.

The database comes from, most specific first: a connection handed in by the
caller (``supervisor.db.migrate`` runs migrations at boot this way, under a
lock), ``-x url=...``, ``DATABASE_URL`` (async drivers mapped to their sync
equivalents), then ``sqlalchemy.url`` in alembic.ini.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

from supervisor.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_SYNC_DRIVERS = {"postgresql+asyncpg": "postgresql+psycopg2", "sqlite+aiosqlite": "sqlite"}


def sync_url(url: str) -> str:
    """The same database through a synchronous driver (Alembic runs synchronously)."""
    scheme, sep, rest = url.partition("://")
    return f"{_SYNC_DRIVERS.get(scheme, scheme)}{sep}{rest}"


def _url() -> str:
    explicit = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get("DATABASE_URL")
    return sync_url(explicit) if explicit else str(config.get_main_option("sqlalchemy.url"))


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,  # SQLite cannot ALTER constraints; batch mode copies the table
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as conn:
        _configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
