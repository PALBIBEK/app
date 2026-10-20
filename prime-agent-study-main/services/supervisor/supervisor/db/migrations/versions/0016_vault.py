"""Alembic revision: the vault (sealed secrets by owner and name)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_vault"
down_revision: str | None = "0015_drop_unused_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_secrets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_kind", "owner_id", "name", name="uq_vault_secrets_owner_name"),
    )


def downgrade() -> None:
    op.drop_table("vault_secrets")
