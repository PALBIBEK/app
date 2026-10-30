"""Environments and the bind pins' comments: what the models gained without a migration.

``environments`` was added to the models alone (development's ``create_all`` made it; a
migrated database never had it). A database adopted from ``create_all`` may already hold
it, so it is created only when missing.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-31 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_BIND_COMMENTS = {
    "bind_status": (sa.String(length=32), False, "unbound | bound | stale | error"),
    "harness_version": (sa.String(length=128), True, "AI harness_version pin (e.g. githi-v1)"),
    "runtime": (sa.String(length=64), True, "AI runtime pin (e.g. harness | echo)"),
    "composition_id": (sa.String(length=128), True, "Composition artifact id"),
    "composition_digest": (sa.String(length=80), True, "Immutable composition digest sha256:…"),
    "bound_at": (sa.DateTime(timezone=True), True, "When the harness was last bound"),
    "bound_by": (sa.String(length=36), True, "User id that performed the bind"),
}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("environments"):
        op.create_table(
            "environments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
            ),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("spec", JSON_DOCUMENT, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "name", name="uq_environments_user_name"),
        )
        op.create_index("ix_environments_user_id", "environments", ["user_id"])
    for column, (type_, nullable, comment) in _BIND_COMMENTS.items():
        op.alter_column("harnesses", column, existing_type=type_, existing_nullable=nullable, comment=comment)


def downgrade() -> None:
    for column, (type_, nullable, _) in _BIND_COMMENTS.items():
        op.alter_column("harnesses", column, existing_type=type_, existing_nullable=nullable, comment=None)
    op.drop_index("ix_environments_user_id", table_name="environments")
    op.drop_table("environments")
