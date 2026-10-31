"""Alembic revision: the task a subagent was given (``supervisor.session.subagent_view``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_subagent_task"
down_revision: str | None = "0032_subagent_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("subagent_task", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "subagent_task")
