"""Add session_links.scope_id for agent /workspace durable bind (D9).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-31 06:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_links",
        sa.Column("scope_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_links", "scope_id")
