"""Alembic revision: each tenant's spend per calendar month (``supervisor.policy.budget``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_tenant_spend"
down_revision: str | None = "0028_session_holder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_spend",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("month", sa.String(length=7), primary_key=True),
        sa.Column("cost_usd", sa.Numeric(18, 8), nullable=False),
    )
    # Counting starts here: the cap is per month, and earlier spend is not attributed to one.


def downgrade() -> None:
    op.drop_table("tenant_spend")
