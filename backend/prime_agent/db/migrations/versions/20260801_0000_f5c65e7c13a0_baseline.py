"""Baseline: the schema the first migration builds on (users, sign-ins, tools, harnesses).

The chain always started at this revision, but its file was never committed, so the
migrations could not be applied. These are the tables as they stood then (the codebase's
first import); later revisions add to them.

Revision ID: f5c65e7c13a0
Revises:
Create Date: 2026-08-01 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5c65e7c13a0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("avatar", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'active'"), nullable=False),
        sa.Column("parameters", JSON_DOCUMENT, server_default="[]", nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=True),
        sa.Column("auth_required", sa.Boolean(), nullable=False),
        sa.Column("rate_limit", sa.Integer(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint("user_id", "name", name="uq_tools_user_id_name"),
    )
    op.create_index("ix_tools_user_id", "tools", ["user_id"])
    op.create_index("ix_tools_name", "tools", ["name"])

    op.create_table(
        "harnesses",
        sa.Column("id", sa.String(length=36), primary_key=True, comment="UUID primary key"),
        sa.Column(
            "user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
            comment="User that owns this harness",
        ),
        sa.Column("name", sa.String(length=255), nullable=False, comment="Harness name (unique)"),
        sa.Column("description", sa.Text(), nullable=True, comment="Optional description"),
        sa.Column(
            "model", sa.String(length=100), nullable=False,
            comment="LLM model: claude-sonnet-4, claude-opus-4, gpt-4-turbo, gpt-4o",
        ),
        sa.Column(
            "mode", sa.String(length=50), server_default=sa.text("'chat'"), nullable=False,
            comment="Execution mode: chat, completion, agent",
        ),
        sa.Column(
            "tools", JSON_DOCUMENT, server_default="[]", nullable=False,
            comment="List of tool IDs (stored as JSON array)",
        ),
        sa.Column(
            "memory", JSON_DOCUMENT, server_default="[]", nullable=False,
            comment="List of memory store IDs (stored as JSON array)",
        ),
        sa.Column("system_prompt", sa.Text(), nullable=True, comment="System prompt for the agent"),
        sa.Column("temperature", sa.Float(), nullable=True, comment="Sampling temperature (0-2)"),
        sa.Column("max_tokens", sa.Integer(), nullable=True, comment="Maximum tokens to generate"),
        *_audit_columns(),
        sa.UniqueConstraint("user_id", "name", name="uq_harnesses_user_id_name"),
    )
    op.create_index("ix_harnesses_user_id", "harnesses", ["user_id"])
    op.create_index("ix_harnesses_name", "harnesses", ["name"])


def downgrade() -> None:
    op.drop_table("harnesses")
    op.drop_table("tools")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
