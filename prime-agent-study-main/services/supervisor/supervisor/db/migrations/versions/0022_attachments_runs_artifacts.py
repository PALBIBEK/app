"""Alembic revision: conversation attachments, run inputs and artifacts.

* ``session_attachments``: a workspace file attached to a session, pinned to one version.
* ``run_inputs``: what each run started from (attachments and workspace files, by version).
* ``artifacts``: files a run produced for the user, collected by the supervisor.
* ``workspace_file_versions.source``: where a version's bytes came from (an agent run, a saved
  artifact) when not a person's save.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_attachments_runs_artifacts"
down_revision: str | None = "0021_workspace_file_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workspace_file_versions", sa.Column("source", sa.Text(), nullable=True))
    op.create_table(
        "session_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("attached_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_session_attachments_session_id", "session_attachments", ["session_id"])
    op.create_table(
        "run_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_inputs_session_turn", "run_inputs", ["session_id", "turn_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_scope_id", sa.String(36), nullable=True),
        sa.Column("saved_path", sa.Text(), nullable=True),
        sa.Column("saved_version", sa.Integer(), nullable=True),
    )
    op.create_index("ix_artifacts_session", "artifacts", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_session", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_run_inputs_session_turn", table_name="run_inputs")
    op.drop_table("run_inputs")
    op.drop_index("ix_session_attachments_session_id", table_name="session_attachments")
    op.drop_table("session_attachments")
    op.drop_column("workspace_file_versions", "source")
