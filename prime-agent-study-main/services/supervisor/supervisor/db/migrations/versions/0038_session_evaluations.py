"""Alembic revision: sessions that replay saved runs for an experiment.

An experiment's runs were judged on the same sample as live traffic, so with a judge at
less than 100% the two versions it compares were judged by different judges, and the
improvement gate refused them as "not judged on both". A session marked here is judged by
every enabled judge (``supervisor.traces.judges.judge_run``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_session_evaluations"
down_revision: str | None = "0037_journal_blob_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_evaluations",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("session_evaluations")
