"""Alembic revision: whether a subagent runs in the background (``supervisor.session.children``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_subagent_kind"
down_revision: str | None = "0031_subagent_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("subagent_kind", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "subagent_kind")
