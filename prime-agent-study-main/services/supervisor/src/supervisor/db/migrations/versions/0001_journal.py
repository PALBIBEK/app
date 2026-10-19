"""Alembic revision: journal schema (sessions, events, session_index)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_journal"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("harness_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="-1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
    )
    op.create_table(
        "session_index",
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("sessions.id"), primary_key=True
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("session_index")
    op.drop_table("events")
    op.drop_table("sessions")
