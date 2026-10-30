"""Alembic revision: the ids of erased sessions (``supervisor.session.erase``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_erased_sessions"
down_revision: str | None = "0029_tenant_spend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erased_sessions",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_erased_sessions_tenant_id", "erased_sessions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_erased_sessions_tenant_id", table_name="erased_sessions")
    op.drop_table("erased_sessions")
