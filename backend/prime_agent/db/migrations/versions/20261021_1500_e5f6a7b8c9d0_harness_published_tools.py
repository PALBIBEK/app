"""Harnesses remember their live release's tool names (for lists and cards).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-21 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("harnesses", sa.Column(
        "published_tools", JSON_DOCUMENT, nullable=True,
        comment="Built-in tool names of the live release's manifest (null until published)",
    ))


def downgrade() -> None:
    op.drop_column("harnesses", "published_tools")
