"""Alembic revision: the journal records which blobs it keeps and which events name them
(supervisor.journal.blobrefs), read once here from the events already journalled.

Erasing a session found the blobs still used by scanning every journal's payloads; the
orphan sweep needs to know every blob kept. Blobs stored before this are recorded as of
now (the sweep's grace starts here); one already orphaned is not known and stays.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0037_journal_blob_refs"
down_revision: str | None = "0036_session_index_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_blobs",
        sa.Column("ref", sa.String(64), primary_key=True),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "journal_blob_refs",
        sa.Column("ref", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
    )
    op.create_index("ix_journal_blob_refs_session", "journal_blob_refs", ["session_id"])

    events = sa.table("events", sa.column("session_id"), sa.column("seq"), sa.column("payload", sa.JSON))
    refs = sa.table("journal_blob_refs", sa.column("ref"), sa.column("session_id"), sa.column("seq"))
    for field in ("_blob_ref", "logs_ref"):
        named = events.c.payload[field].as_string()
        op.execute(
            refs.insert().from_select(
                ["ref", "session_id", "seq"],
                sa.select(named, events.c.session_id, events.c.seq).where(named.is_not(None)),
            )
        )
    blobs = sa.table("journal_blobs", sa.column("ref"), sa.column("stored_at", sa.DateTime(timezone=True)))
    op.execute(
        blobs.insert().from_select(
            ["ref", "stored_at"],
            sa.select(refs.c.ref, sa.literal(datetime.now(UTC), sa.DateTime(timezone=True))).distinct(),
        )
    )


def downgrade() -> None:
    op.drop_index("ix_journal_blob_refs_session", table_name="journal_blob_refs")
    op.drop_table("journal_blob_refs")
    op.drop_table("journal_blobs")
