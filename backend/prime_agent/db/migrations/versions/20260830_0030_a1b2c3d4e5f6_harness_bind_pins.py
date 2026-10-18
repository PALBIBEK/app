"""Add D3 harness bind / composition pin columns.

Revision ID: a1b2c3d4e5f6
Revises: f5c65e7c13a0
Create Date: 2026-08-30 00:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f5c65e7c13a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "harnesses",
        sa.Column(
            "bind_status",
            sa.String(length=32),
            server_default=sa.text("'unbound'"),
            nullable=False,
        ),
    )
    op.add_column(
        "harnesses",
        sa.Column("harness_version", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "harnesses",
        sa.Column("runtime", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "harnesses",
        sa.Column("composition_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "harnesses",
        sa.Column("composition_digest", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "harnesses",
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "harnesses",
        sa.Column("bound_by", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("harnesses", "bound_by")
    op.drop_column("harnesses", "bound_at")
    op.drop_column("harnesses", "composition_digest")
    op.drop_column("harnesses", "composition_id")
    op.drop_column("harnesses", "runtime")
    op.drop_column("harnesses", "harness_version")
    op.drop_column("harnesses", "bind_status")
