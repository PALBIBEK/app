"""Alembic revision: catch the migrations up with the models.

The application created its schema with ``create_all`` while these migrations
fell behind; from here on the migrations are the schema (``supervisor.db.
migrate`` runs them at boot, and CI fails if the models drift from them).

Adds the tables the models gained (catalog errors and usage, the tool outbox),
the approval responder (who answered, when), catalog artifact ownership and
deprecation audit, and the uniqueness the models declare; drops the sessions ->
harness_versions foreign key the models no longer have (sessions may pin a
harness version loaded from files that is not registered in that table).
Existing catalog artifacts become shipped ones: owner and creator "system".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_catch_up"
down_revision: str | None = "0012_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_errors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("catalog_errors", schema=None) as batch_op:
        batch_op.create_index(
            "ix_catalog_errors_artifact", ["kind", "artifact_id", "digest"], unique=False
        )
        batch_op.create_index("ix_catalog_errors_code", ["error_code"], unique=False)
        batch_op.create_index("ix_catalog_errors_timestamp", ["timestamp_ms"], unique=False)

    op.create_table(
        "catalog_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("catalog_usage", schema=None) as batch_op:
        batch_op.create_index(
            "ix_catalog_usage_artifact", ["kind", "artifact_id", "digest"], unique=False
        )
        batch_op.create_index("ix_catalog_usage_principal", ["principal_id"], unique=False)
        batch_op.create_index("ix_catalog_usage_session", ["session_id"], unique=False)
        batch_op.create_index("ix_catalog_usage_timestamp", ["timestamp_ms"], unique=False)

    op.create_table(
        "tool_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("tool_call_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_tool_outbox_idem"),
    )
    with op.batch_alter_table("tool_outbox", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tool_outbox_session_id"), ["session_id"], unique=False)

    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("responded_by", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("catalog_artifacts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'"))
        )
        batch_op.add_column(sa.Column("owner_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("deprecated_by", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_catalog_artifacts_created_by", ["created_by"], unique=False)
        batch_op.create_index("ix_catalog_artifacts_owner_id", ["owner_id"], unique=False)
        batch_op.create_index("ix_catalog_artifacts_status", ["status"], unique=False)

    op.execute("UPDATE catalog_artifacts SET owner_id = created_by WHERE owner_id IS NULL")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_sessions_harness_version"), type_="foreignkey")


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_sessions_harness_version"),
            "harness_versions",
            ["harness_version"],
            ["harness_version"],
        )

    with op.batch_alter_table("catalog_artifacts", schema=None) as batch_op:
        batch_op.drop_index("ix_catalog_artifacts_status")
        batch_op.drop_index("ix_catalog_artifacts_owner_id")
        batch_op.drop_index("ix_catalog_artifacts_created_by")
        batch_op.drop_column("deprecated_at")
        batch_op.drop_column("deprecated_by")
        batch_op.drop_column("owner_id")
        batch_op.drop_column("created_by")

    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_column("responded_at")
        batch_op.drop_column("responded_by")

    with op.batch_alter_table("tool_outbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tool_outbox_session_id"))

    op.drop_table("tool_outbox")
    with op.batch_alter_table("catalog_usage", schema=None) as batch_op:
        batch_op.drop_index("ix_catalog_usage_timestamp")
        batch_op.drop_index("ix_catalog_usage_session")
        batch_op.drop_index("ix_catalog_usage_principal")
        batch_op.drop_index("ix_catalog_usage_artifact")

    op.drop_table("catalog_usage")
    with op.batch_alter_table("catalog_errors", schema=None) as batch_op:
        batch_op.drop_index("ix_catalog_errors_timestamp")
        batch_op.drop_index("ix_catalog_errors_code")
        batch_op.drop_index("ix_catalog_errors_artifact")

    op.drop_table("catalog_errors")
