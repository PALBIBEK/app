"""Alembic revision: scope memberships/grants + sessions.scope_id (Track B WP-M4)."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_workspace_authz"
down_revision: Union[str, None] = "0007_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scope_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"]),
        sa.UniqueConstraint(
            "scope_id", "principal_id", name="uq_scope_memberships_scope_principal"
        ),
    )
    op.create_index(
        "ix_scope_memberships_scope_id", "scope_memberships", ["scope_id"]
    )
    op.create_index(
        "ix_scope_memberships_principal_id", "scope_memberships", ["principal_id"]
    )

    op.create_table(
        "scope_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("grant", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"]),
        sa.UniqueConstraint(
            "scope_id",
            "principal_id",
            "grant",
            name="uq_scope_grants_scope_principal_grant",
        ),
    )
    op.create_index("ix_scope_grants_scope_id", "scope_grants", ["scope_id"])
    op.create_index("ix_scope_grants_principal_id", "scope_grants", ["principal_id"])

    op.add_column(
        "sessions",
        sa.Column("scope_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_sessions_scope_id", "sessions", ["scope_id"])
    op.create_foreign_key(
        "fk_sessions_scope_id_scopes",
        "sessions",
        "scopes",
        ["scope_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_scope_id_scopes", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_scope_id", table_name="sessions")
    op.drop_column("sessions", "scope_id")
    op.drop_index("ix_scope_grants_principal_id", table_name="scope_grants")
    op.drop_index("ix_scope_grants_scope_id", table_name="scope_grants")
    op.drop_table("scope_grants")
    op.drop_index("ix_scope_memberships_principal_id", table_name="scope_memberships")
    op.drop_index("ix_scope_memberships_scope_id", table_name="scope_memberships")
    op.drop_table("scope_memberships")
