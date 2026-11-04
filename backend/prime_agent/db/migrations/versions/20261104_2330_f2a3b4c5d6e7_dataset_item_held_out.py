"""A saved run can be held out: kept from the optimizer, so the gate measures generalisation.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-04 23:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has it already.
    if "held_out" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_dataset_items")}:
        with op.batch_alter_table("trace_dataset_items") as batch:
            batch.add_column(sa.Column("held_out", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("trace_dataset_items") as batch:
        batch.drop_column("held_out")
