"""Harness drafts and releases (the manifest builder).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-21 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "harness_drafts",
        sa.Column("harness_id", sa.String(36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("manifest", JSON_DOCUMENT, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "harness_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("harness_id", sa.String(36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("manifest", JSON_DOCUMENT, nullable=False),
        sa.Column("composition_id", sa.String(128), nullable=False),
        sa.Column("composition_digest", sa.String(80), nullable=False),
        sa.Column("harness_version", sa.String(128), nullable=False),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("harness_id", "version", name="uq_harness_releases_version"),
    )
    op.create_index("ix_harness_releases_harness_id", "harness_releases", ["harness_id"])


def downgrade() -> None:
    op.drop_index("ix_harness_releases_harness_id", table_name="harness_releases")
    op.drop_table("harness_releases")
    op.drop_table("harness_drafts")
