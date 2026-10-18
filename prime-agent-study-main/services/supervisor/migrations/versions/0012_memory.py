"""Alembic revision: long-term memory (notebooks, facts, revisions) and session owners.

Sessions gain their owner, the exact agent version they pin and their notebook
binding; the session index gains an archived flag. Existing rows keep NULLs:
a session created before memory has no owner and no notebooks.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_memory"
down_revision: str | None = "0011_events_event_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "memory_notebooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("owner_principal_id", sa.Text, nullable=False),
        sa.Column("agent_pin", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("archived_at", sa.BigInteger, nullable=True),
        sa.Column("last_tidied_at", sa.BigInteger, nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "owner_principal_id", "agent_pin", "name", name="uq_memory_notebook_name"
        ),
    )
    op.create_index(
        "ix_memory_notebooks_owner_pin",
        "memory_notebooks",
        ["tenant_id", "owner_principal_id", "agent_pin"],
    )
    fact_columns = [
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("sensitivity", sa.Text, nullable=False),
        sa.Column("sources", sa.JSON, nullable=False),
        sa.Column("source_unknown", sa.Boolean, nullable=False),
        sa.Column("author", sa.Text, nullable=True),
    ]
    op.create_table(
        "memory_facts",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column(
            "notebook_id", sa.String(36), sa.ForeignKey("memory_notebooks.id"), nullable=False
        ),
        *fact_columns,
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.Column("deleted_at", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "ix_memory_facts_notebook_live", "memory_facts", ["notebook_id", "deleted_at", "id"]
    )
    op.create_table(
        "memory_fact_revisions",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column(
            "notebook_id", sa.String(36), sa.ForeignKey("memory_notebooks.id"), nullable=False
        ),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("fact_id", sa.BigInteger, nullable=False),
        sa.Column("op", sa.Text, nullable=False),
        *[c.copy() for c in fact_columns],
        sa.Column("at", sa.BigInteger, nullable=False),
    )
    op.create_index(
        "ix_memory_revisions_notebook", "memory_fact_revisions", ["notebook_id", "revision"]
    )
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("owner_principal_id", sa.Text, nullable=True))
        batch.add_column(sa.Column("agent_pin", sa.Text, nullable=True))
        batch.add_column(sa.Column("memory", sa.JSON, nullable=True))
        batch.create_index(
            "ix_sessions_owner_pin", ["tenant_id", "owner_principal_id", "agent_pin"]
        )
    with op.batch_alter_table("session_index") as batch:
        batch.add_column(
            sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("session_index") as batch:
        batch.drop_column("archived")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_owner_pin")
        batch.drop_column("memory")
        batch.drop_column("agent_pin")
        batch.drop_column("owner_principal_id")
    op.drop_table("memory_fact_revisions")
    op.drop_table("memory_facts")
    op.drop_table("memory_notebooks")
