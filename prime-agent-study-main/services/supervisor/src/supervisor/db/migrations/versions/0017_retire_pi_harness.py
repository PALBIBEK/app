"""Alembic revision: retire the Pi harness runtime ("harness"), removed from the supervisor.

* Harness versions registered on that runtime are deleted: loading one now
  fails validation (an unknown runtime) and would stop the supervisor booting.
* Sessions on it can no longer resume, so the ones still open are marked failed.
* Compositions on a retired version are deprecated: they start no new sessions.

Data only; downgrade cannot bring the runtime back, so it changes nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_retire_pi_harness"
down_revision: str | None = "0016_vault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME = "harness"
_SHIPPED = ("coding-v1",)  # shipped from the catalog directory, never a harness_versions row


def upgrade() -> None:
    bind = op.get_bind()
    retired = set(_SHIPPED) | {
        row[0]
        for row in bind.execute(
            sa.text("SELECT harness_version FROM harness_versions WHERE runtime = :r"), {"r": _RUNTIME}
        )
    }
    bind.execute(sa.text("DELETE FROM harness_versions WHERE runtime = :r"), {"r": _RUNTIME})
    bind.execute(
        sa.text(
            "UPDATE sessions SET status = 'failed' "
            "WHERE runtime = :r AND status NOT IN ('terminated', 'failed')"
        ),
        {"r": _RUNTIME},
    )
    compositions = sa.table(
        "catalog_artifacts",
        sa.column("kind", sa.Text),
        sa.column("artifact_id", sa.Text),
        sa.column("digest", sa.Text),
        sa.column("status", sa.Text),
        sa.column("body", sa.JSON),
    )
    rows = bind.execute(
        sa.select(compositions.c.artifact_id, compositions.c.digest, compositions.c.body).where(
            compositions.c.kind == "composition", compositions.c.status != "deprecated"
        )
    )
    for artifact_id, digest, body in rows.fetchall():
        if isinstance(body, dict) and body.get("harness_version") in retired:
            bind.execute(
                compositions.update()
                .where(
                    compositions.c.kind == "composition",
                    compositions.c.artifact_id == artifact_id,
                    compositions.c.digest == digest,
                )
                .values(status="deprecated")
            )


def downgrade() -> None:
    pass
