"""Alembic revision: workspace file history.

``workspace_file_versions`` keeps every saved state of a path (its object key, SHA-256, size
and type), so earlier versions can be listed, read and restored. Each file's current state is
copied in as its first recorded version; nothing older than that was kept before.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0021_workspace_file_versions"
down_revision: str | None = "0020_notebooks_move"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    versions = op.create_table(
        "workspace_file_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_id", sa.String(36), sa.ForeignKey("scopes.id"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_id", "path", "version", name="uq_workspace_file_versions_path_version"),
    )
    op.create_index(
        "ix_workspace_file_versions_scope_sha", "workspace_file_versions", ["scope_id", "content_sha256"]
    )
    rows = op.get_bind().execute(
        sa.text(
            "SELECT scope_id, path, version, content_sha256, object_key, size_bytes, mime_type, "
            "updated_at FROM workspace_files"
        )
    ).all()
    if rows:
        op.bulk_insert(
            versions,
            [
                {
                    "id": uuid4().hex,
                    "scope_id": r.scope_id,
                    "path": r.path,
                    "version": int(r.version),
                    "content_sha256": r.content_sha256,
                    "object_key": r.object_key,
                    "size_bytes": int(r.size_bytes),
                    "mime_type": r.mime_type,
                    "author": None,
                    "restored_from": None,
                    "created_at": r.updated_at or datetime.now(UTC),
                }
                for r in rows
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_workspace_file_versions_scope_sha", table_name="workspace_file_versions")
    op.drop_table("workspace_file_versions")
