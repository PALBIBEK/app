"""A change can be started from an issue: the proposal keeps which.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-11-05 04:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has it already.
    if "issue_key" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_proposals")}:
        with op.batch_alter_table("trace_proposals") as batch:
            batch.add_column(sa.Column("issue_key", sa.String(length=600), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_proposals") as batch:
        batch.drop_column("issue_key")
