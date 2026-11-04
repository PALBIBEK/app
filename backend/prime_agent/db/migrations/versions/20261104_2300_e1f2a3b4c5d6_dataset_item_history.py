"""A saved run keeps the earlier messages of its conversation, replayed before it.

An experiment replayed only the saved run's own message, in a fresh session, so a run that
answered a follow-up was measured as if it were the first message.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-04 23:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has it already.
    if "history" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_dataset_items")}:
        with op.batch_alter_table("trace_dataset_items") as batch:
            batch.add_column(sa.Column("history", JSON_DOCUMENT, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_dataset_items") as batch:
        batch.drop_column("history")
