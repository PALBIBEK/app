"""Proposals beat while they run, and an agent has one running at a time, by an index.

A proposal cut off by a server stop stayed "drafting" or "evaluating" forever and blocked its
agent; it now carries a heartbeat (``services.heartbeat``). Two requests could both start a
proposal for the same agent; a partial unique index now refuses the second. Where an agent
already has more than one active, all but the newest are settled as failed first, so the index
can be built; a stuck one left is settled by the heartbeat rule when next read.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-05 05:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = "status IN ('drafting', 'evaluating')"
_INDEX = "uq_trace_proposals_active"


def upgrade() -> None:
    found = sa.inspect(op.get_bind())
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has them already.
    if "heartbeat_at" not in {c["name"] for c in found.get_columns("trace_proposals")}:
        with op.batch_alter_table("trace_proposals") as batch:
            batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    if _INDEX not in {i["name"] for i in found.get_indexes("trace_proposals")}:
        op.execute(sa.text(
            "UPDATE trace_proposals SET status = 'failed', "
            "error = 'another change for this agent was started at the same time' "
            f"WHERE {_ACTIVE} AND EXISTS (SELECT 1 FROM trace_proposals newer "
            "WHERE newer.harness_id = trace_proposals.harness_id "
            "AND newer.status IN ('drafting', 'evaluating') AND newer.created_at > trace_proposals.created_at)"
        ))
        op.create_index(_INDEX, "trace_proposals", ["harness_id"], unique=True,
                        postgresql_where=sa.text(_ACTIVE), sqlite_where=sa.text(_ACTIVE))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="trace_proposals")
    with op.batch_alter_table("trace_proposals") as batch:
        batch.drop_column("heartbeat_at")
