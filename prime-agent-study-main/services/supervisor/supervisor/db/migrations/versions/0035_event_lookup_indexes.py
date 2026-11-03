"""Alembic revision: indexes for how the journal is read besides in order.

The primary key (session_id, seq) serves reading a session in order. Everything else
scanned the session's whole journal, or every journal:

- a session's latest events of a type (its last RunEnd, MemoryContext, run boundary:
  every model step and every busy check) → (session_id, type, seq);
- one run's events (``read_turn``, ``read_run_window``) → (session_id, turn_id, seq);
- events of a type across sessions in time order (latency, traces backfill) →
  (type, ts_ms).

They are built in the migrations' transaction, which holds appends to ``events`` while
they build. On a large table, build them first without that (``CREATE INDEX
CONCURRENTLY IF NOT EXISTS <name> ON events (<columns>)`` for each below, by hand): this
migration then finds them and only records itself. It cannot build them concurrently
itself: every process starting waits for the migrations' lock inside a transaction, and a
concurrent build waits for every open transaction to end.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035_event_lookup_indexes"
down_revision: str | None = "0034_unique_event_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: dict[str, list[str]] = {
    "ix_events_session_type_seq": ["session_id", "type", "seq"],
    "ix_events_session_turn_seq": ["session_id", "turn_id", "seq"],
    "ix_events_type_ts": ["type", "ts_ms"],
}


def upgrade() -> None:
    for name, columns in INDEXES.items():
        op.create_index(name, "events", columns, if_not_exists=True)


def downgrade() -> None:
    for name in INDEXES:
        op.drop_index(name, table_name="events", if_exists=True)
