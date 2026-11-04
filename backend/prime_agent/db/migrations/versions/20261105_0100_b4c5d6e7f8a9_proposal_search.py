"""A proposal may search: try a few candidates, each built on the best so far.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-05 01:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has them already.
    held = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_proposals")}
    with op.batch_alter_table("trace_proposals") as batch:
        if "max_rounds" not in held:
            batch.add_column(sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="1"))
        if "rounds" not in held:
            batch.add_column(sa.Column("rounds", JSON_DOCUMENT, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_proposals") as batch:
        batch.drop_column("rounds")
        batch.drop_column("max_rounds")
