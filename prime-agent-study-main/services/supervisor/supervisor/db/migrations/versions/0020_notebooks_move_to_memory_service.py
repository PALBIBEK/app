"""Alembic revision: notebooks are stored by the memory service, not here.

The supervisor's memory_notebooks / memory_facts / memory_fact_revisions tables
are no longer used. Empty, they are dropped. With rows in them they are renamed
to ``legacy_*`` instead, so a deployment that had notebooks loses nothing; their
contents can then be moved into the memory service and the legacy tables dropped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_notebooks_move"
down_revision: str | None = "0019_runners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("memory_fact_revisions", "memory_facts", "memory_notebooks")  # children first
logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    present = [t for t in _TABLES if sa.inspect(bind).has_table(t)]
    rows = sum(bind.execute(sa.text(f"SELECT COUNT(*) FROM {t}")).scalar_one() for t in present)
    for table in present:
        if rows:
            op.rename_table(table, f"legacy_{table}")
        else:
            op.drop_table(table)
    if rows:
        logger.warning(
            "notebooks moved to the memory service: %d existing rows kept in legacy_* tables "
            "(move them into the memory service, then drop the tables)",
            rows,
        )


def downgrade() -> None:
    raise NotImplementedError("notebooks live in the memory service; there is no way back here")
