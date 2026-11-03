"""Alembic revision: an event id is unique within its session, so a keyed write lands once.

Two attempts of one step (Temporal starts the next while the last may still run) could
each append the event its key names. A journal that already holds such a pair keeps both
events (it is append-only): every copy after the first gets a fresh id of its own, so the
key still finds the first and the index can be unique.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0034_unique_event_ids"
down_revision: str | None = "0033_subagent_task"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    copies = conn.execute(
        sa.text(
            "SELECT e.session_id, e.seq FROM events e JOIN ("
            "  SELECT session_id, event_id, min(seq) AS first FROM events"
            "  GROUP BY session_id, event_id HAVING count(*) > 1"
            ") d ON d.session_id = e.session_id AND d.event_id = e.event_id AND e.seq > d.first"
        )
    ).all()
    for session_id, seq in copies:
        conn.execute(
            sa.text("UPDATE events SET event_id = :id WHERE session_id = :s AND seq = :seq"),
            {"id": uuid4().hex, "s": session_id, "seq": seq},
        )
    op.drop_index("ix_events_session_event_id", table_name="events")
    op.create_index("uq_events_session_event_id", "events", ["session_id", "event_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_events_session_event_id", table_name="events")
    op.create_index("ix_events_session_event_id", "events", ["session_id", "event_id"])
