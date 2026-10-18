"""Add session_links ownership table for D4.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-30 00:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("harness_id", sa.String(length=36), nullable=False),
        sa.Column("ai_session_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("harness_version", sa.String(length=128), nullable=False),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("composition_id", sa.String(length=128), nullable=True),
        sa.Column("composition_digest", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["harness_id"], ["harnesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_session_id", name="uq_session_links_ai_session_id"),
    )
    op.create_index("ix_session_links_user_id", "session_links", ["user_id"])
    op.create_index("ix_session_links_harness_id", "session_links", ["harness_id"])


def downgrade() -> None:
    op.drop_index("ix_session_links_harness_id", table_name="session_links")
    op.drop_index("ix_session_links_user_id", table_name="session_links")
    op.drop_table("session_links")
