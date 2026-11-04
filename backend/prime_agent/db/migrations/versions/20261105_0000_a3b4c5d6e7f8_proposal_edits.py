"""A proposal keeps the itemized edits it made, those it could not, and what it expects.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-05 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_COLUMNS = (
    ("edits", JSON_DOCUMENT),
    ("skipped_edits", JSON_DOCUMENT),
    ("expected_outcome", sa.Text()),
)


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has them already.
    held = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("trace_proposals")}
    missing = [(name, type_) for name, type_ in _COLUMNS if name not in held]
    if missing:
        with op.batch_alter_table("trace_proposals") as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_proposals") as batch:
        for name, _ in reversed(_COLUMNS):
            batch.drop_column(name)
