"""Alembic: partial unique live paths on workspace_files (Stage 1 / D9)."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_workspace_partial_unique"
down_revision: Union[str, None] = "0008_workspace_authz"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_workspace_files_scope_path", "workspace_files", type_="unique"
    )
    op.create_index(
        "uq_workspace_files_scope_path_live",
        "workspace_files",
        ["scope_id", "path"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workspace_files_scope_path_live",
        table_name="workspace_files",
    )
    op.create_unique_constraint(
        "uq_workspace_files_scope_path",
        "workspace_files",
        ["scope_id", "path"],
    )
