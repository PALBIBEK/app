"""Alembic revision: catalog_artifacts for persisted composition catalog."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_catalog_artifacts"
down_revision: Union[str, None] = "0005_composition_pins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_artifacts",
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column("digest", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="offered"),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "artifact_id", "digest", name="uq_catalog_artifact"),
    )


def downgrade() -> None:
    op.drop_table("catalog_artifacts")
