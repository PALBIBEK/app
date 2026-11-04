"""An approved change is watched live against the version before it, and rolled back if worse.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-11-05 03:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has it already.
    if "watch" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_proposals")}:
        with op.batch_alter_table("trace_proposals") as batch:
            batch.add_column(sa.Column("watch", JSON_DOCUMENT, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_proposals") as batch:
        batch.drop_column("watch")
