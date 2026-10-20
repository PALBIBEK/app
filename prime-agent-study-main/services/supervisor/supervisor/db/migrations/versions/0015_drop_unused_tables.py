"""Alembic revision: drop the tables no code reads or writes.

``sandbox_routes`` (0010) served a dynamic sandbox router that was never wired,
and ``catalog_errors`` / ``catalog_usage`` (0013) an analytics layer nothing
called; their models went with that code (ec82945). Without this revision the
migrated schema and the models disagree, which ``alembic check`` refuses.
Downgrade recreates them empty, exactly as their creating revisions did.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_drop_unused_tables"
down_revision: str | None = "0014_harness_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: dict[str, dict[str, list[str]]] = {
    "sandbox_routes": {"ix_sandbox_routes_backend": ["backend"]},
    "catalog_errors": {
        "ix_catalog_errors_artifact": ["kind", "artifact_id", "digest"],
        "ix_catalog_errors_code": ["error_code"],
        "ix_catalog_errors_timestamp": ["timestamp_ms"],
    },
    "catalog_usage": {
        "ix_catalog_usage_artifact": ["kind", "artifact_id", "digest"],
        "ix_catalog_usage_principal": ["principal_id"],
        "ix_catalog_usage_session": ["session_id"],
        "ix_catalog_usage_timestamp": ["timestamp_ms"],
    },
}


def upgrade() -> None:
    for table, indexes in _INDEXES.items():
        for name in indexes:
            op.drop_index(name, table_name=table)
        op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "sandbox_routes",
        sa.Column("scope_id", sa.String(length=64), primary_key=True),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migrated_from", sa.String(length=32), nullable=True),
        sa.Column("migration_sha", sa.String(length=64), nullable=True),
        sa.Column("capabilities_lost", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "catalog_errors",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "catalog_usage",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    for table, indexes in _INDEXES.items():
        for name, columns in indexes.items():
            op.create_index(name, table, columns)
