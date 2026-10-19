"""Approval recovery — context preservation across restarts.

Stores context needed to resume approval workflow after process restart.
Context includes:
- Tool call state (tool_call_id, arguments)
- Session state snapshot
- Approval metadata

Now with full PostgreSQL persistence (QM pattern).

Reference: QM's StoredApproval and recoveredApprovalContext() pattern.
"""

from __future__ import annotations

import logging
import time
from typing import Final

__all__ = [
]


_log = logging.getLogger(__name__)


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


# =============================================================================
# SQL Statements
# =============================================================================

# Create table SQL
CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS approval_contexts (
    approval_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    turn_id VARCHAR(128),
    tool_call_id VARCHAR(128),
    tool_name VARCHAR(128),
    arguments_hash VARCHAR(64),
    state_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    recovered BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_approval_ctx_session ON approval_contexts (session_id);
CREATE INDEX IF NOT EXISTS idx_approval_ctx_expires ON approval_contexts (expires_at);
"""

# Insert or update context
UPSERT_SQL: Final[str] = """
INSERT INTO approval_contexts
    (approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot, expires_at)
VALUES
    (:approval_id, :session_id, :turn_id, :tool_call_id, :tool_name, :arguments_hash, :state_snapshot, :expires_at)
ON CONFLICT (approval_id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    turn_id = EXCLUDED.turn_id,
    tool_call_id = EXCLUDED.tool_call_id,
    tool_name = EXCLUDED.tool_name,
    arguments_hash = EXCLUDED.arguments_hash,
    state_snapshot = EXCLUDED.state_snapshot,
    expires_at = EXCLUDED.expires_at
"""

# Recover single context
RECOVER_SQL: Final[str] = """
SELECT approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot
FROM approval_contexts
WHERE approval_id = :approval_id
  AND expires_at > NOW()
  AND recovered = FALSE
"""

# Mark context as recovered
MARK_RECOVERED_SQL: Final[str] = """
UPDATE approval_contexts SET recovered = TRUE WHERE approval_id = :approval_id
"""

# List pending for session
LIST_PENDING_SQL: Final[str] = """
SELECT approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot
FROM approval_contexts
WHERE (:session_id IS NULL OR session_id = :session_id)
  AND expires_at > NOW()
  AND recovered = FALSE
ORDER BY created_at
LIMIT :limit
"""

# Delete expired contexts
CLEANUP_SQL: Final[str] = """
DELETE FROM approval_contexts WHERE expires_at < NOW()
"""


# =============================================================================
# PostgresApprovalRecovery
# =============================================================================

# =============================================================================
# Unified ApprovalRecovery
# =============================================================================

