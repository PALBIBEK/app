"""Alembic revision: index events by (session_id, event_id).

Workflow activities append events with deterministic ids so a retried activity
never writes the same event twice; that lookup needs this index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_events_event_id_index"
down_revision: str | None = "0010_sandbox_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_events_session_event_id", "events", ["session_id", "event_id"])


def downgrade() -> None:
    op.drop_index("ix_events_session_event_id", table_name="events")
