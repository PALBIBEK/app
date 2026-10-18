"""Alembic revision: harness_versions + session manifest snapshot."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_harness"
down_revision: Union[str, None] = "0002_commands"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_versions",
        sa.Column("harness_version", sa.Text(), primary_key=True),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("sessions", sa.Column("manifest", sa.JSON(), nullable=True))
    # FK: sessions.harness_version → harness_versions (soft on SQLite; enforced in app).
    with op.batch_alter_table("sessions") as batch:
        batch.create_foreign_key(
            "fk_sessions_harness_version",
            "harness_versions",
            ["harness_version"],
            ["harness_version"],
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_constraint("fk_sessions_harness_version", type_="foreignkey")
    op.drop_column("sessions", "manifest")
    op.drop_table("harness_versions")
