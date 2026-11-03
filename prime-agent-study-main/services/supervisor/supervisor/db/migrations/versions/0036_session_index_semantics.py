"""Alembic revision: a session's index says what its chat shows (supervisor.journal.index).

``message_count`` was the length of the conversation the model last held (tool results
counted, shrinking at a compaction, set only when a run ended); it is now the messages a
person reads: each one sent, and each reply with text in it. ``title`` was never set; it
is the first line of the first message sent (credentials masked). Both are recomputed here from the journal.
``archived`` was never read or written: dropped.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0036_session_index_semantics"
down_revision: str | None = "0035_event_lookup_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TITLE_MAX = 80


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _title(text: str) -> str | None:
    from supervisor.journal.secrets import mask_text  # a title shows no credential

    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return None
    line = mask_text(line)
    return line if len(line) <= _TITLE_MAX else line[: _TITLE_MAX - 1].rstrip() + "…"


def upgrade() -> None:
    conn = op.get_bind()
    counts: dict[str, int] = {}
    titles: dict[str, str] = {}
    rows = conn.execute(
        sa.text(
            "SELECT session_id, payload FROM events WHERE type = 'MessageEnd' ORDER BY session_id, seq"
        ).execution_options(yield_per=2000)
    )
    for session_id, payload in rows:
        data = json.loads(payload) if isinstance(payload, str) else payload
        if "_blob_ref" in data:  # too large for its row: a long message, so text in it
            counts[session_id] = counts.get(session_id, 0) + 1
            continue
        role, text = data.get("role"), _text(data.get("content"))
        if role == "user" or (role == "assistant" and text.strip()):
            counts[session_id] = counts.get(session_id, 0) + 1
        if role == "user" and session_id not in titles and (title := _title(text)) is not None:
            titles[session_id] = title
    conn.execute(sa.text("UPDATE session_index SET message_count = 0"))
    update = sa.text("UPDATE session_index SET message_count = :n WHERE session_id = :s")
    for session_id, n in counts.items():
        conn.execute(update, {"n": n, "s": session_id})
    name = sa.text("UPDATE session_index SET title = :t WHERE session_id = :s AND title IS NULL")
    for session_id, title in titles.items():
        conn.execute(name, {"t": title, "s": session_id})
    with op.batch_alter_table("session_index") as batch:
        batch.drop_column("archived")


def downgrade() -> None:
    with op.batch_alter_table("session_index") as batch:
        batch.add_column(sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
