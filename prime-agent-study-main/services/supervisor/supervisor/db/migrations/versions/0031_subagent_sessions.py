"""Alembic revision: subagents are sessions (``supervisor.session.children``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_subagent_sessions"
down_revision: str | None = "0030_erased_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("parent_session_id", sa.String(length=36), nullable=True))
    op.add_column("sessions", sa.Column("root_session_id", sa.String(length=36), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("subagent_depth", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("sessions", sa.Column("subagent_mode", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("subagent_label", sa.Text(), nullable=True))
    op.create_index("ix_sessions_parent_session_id", "sessions", ["parent_session_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_parent_session_id", table_name="sessions")
    for column in ("subagent_label", "subagent_mode", "subagent_depth", "root_session_id", "parent_session_id"):
        op.drop_column("sessions", column)
