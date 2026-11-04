"""Experiments replay each saved run several times (trials).

One replay per item compared versions on noise: a model answers differently run to run.
Anthropic's guide to agent evals ("Demystifying evals for AI agents") runs several trials
per task and reads pass@k and pass^k from them.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-04 22:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has these already.
    found = sa.inspect(op.get_bind())
    if "trials" not in {c["name"] for c in found.get_columns("trace_experiments")}:
        with op.batch_alter_table("trace_experiments") as batch:
            batch.add_column(sa.Column("trials", sa.Integer(), nullable=False, server_default="1"))
    if "trial" not in {c["name"] for c in found.get_columns("trace_experiment_runs")}:
        with op.batch_alter_table("trace_experiment_runs") as batch:
            batch.add_column(sa.Column("trial", sa.Integer(), nullable=False, server_default="0"))
    unique = {u["name"] for u in found.get_unique_constraints("trace_experiment_runs")}
    if "uq_trace_experiment_runs_trial" not in unique:
        with op.batch_alter_table("trace_experiment_runs") as batch:
            if "uq_trace_experiment_runs_item" in unique:
                batch.drop_constraint("uq_trace_experiment_runs_item", type_="unique")
            batch.create_unique_constraint("uq_trace_experiment_runs_trial", ["experiment_id", "item_id", "trial"])


def downgrade() -> None:
    op.execute("DELETE FROM trace_experiment_runs WHERE trial > 0")
    with op.batch_alter_table("trace_experiment_runs") as batch:
        batch.drop_constraint("uq_trace_experiment_runs_trial", type_="unique")
        batch.create_unique_constraint("uq_trace_experiment_runs_item", ["experiment_id", "item_id"])
        batch.drop_column("trial")
    with op.batch_alter_table("trace_experiments") as batch:
        batch.drop_column("trials")
