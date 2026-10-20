"""Alembic revision: harness versions registered through the API belong to a tenant.

Existing rows keep a NULL owner: they are shipped versions, usable by every
tenant and changeable by none.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_harness_owner"
down_revision: str | None = "0013_catch_up"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("harness_versions") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("harness_versions") as batch_op:
        batch_op.drop_column("owner_id")
