"""Alembic revision: a replay remembers the saved run's good answer, to grade against.

A dataset item's "good answer" was saved and shown, but nothing graded a run against it.
A replay marked with one is also judged by the built-in ``good answer`` judge
(``supervisor.traces.judges.REFERENCE_JUDGE``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_session_evaluation_expected"
down_revision: str | None = "0038_session_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("session_evaluations") as batch:
        batch.add_column(sa.Column("expected", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("session_evaluations") as batch:
        batch.drop_column("expected")
