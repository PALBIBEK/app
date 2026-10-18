"""Alembic revision: sandbox_routes for dynamic routing.

Maps scopes to backend providers with migration tracking.
Reference: QM's sandbox-routing.ts pattern.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_sandbox_routes"
down_revision: Union[str, None] = "0009_workspace_partial_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sandbox_routes_backend", "sandbox_routes", ["backend"])


def downgrade() -> None:
    op.drop_index("ix_sandbox_routes_backend", table_name="sandbox_routes")
    op.drop_table("sandbox_routes")
