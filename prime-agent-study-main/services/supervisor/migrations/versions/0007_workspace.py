"""Alembic revision: scopes + workspace_files (Track B WP-M2)."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workspace"
down_revision: Union[str, None] = "0006_catalog_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scopes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False, server_default="personal"),
        sa.Column("owner_principal_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspace_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"]),
        sa.UniqueConstraint("scope_id", "path", name="uq_workspace_files_scope_path"),
    )
    op.create_index("ix_workspace_files_scope_id", "workspace_files", ["scope_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_files_scope_id", table_name="workspace_files")
    op.drop_table("workspace_files")
    op.drop_table("scopes")
