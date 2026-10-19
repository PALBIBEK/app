"""PostgreSQL audit logger — persistent audit trail with batching.

Provides database-backed audit logging with:
- Batch writes for performance
- Idempotent logging with ON CONFLICT
- Indexed queries for tail/filter operations
- Automatic table creation if needed

Reference: QM's postgres-audit-log.ts pattern.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    pass


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FLUSH_INTERVAL_MS",
]


_log = logging.getLogger(__name__)

# Default batch size before flush
DEFAULT_BATCH_SIZE: Final[int] = 100

# Default flush interval in milliseconds
DEFAULT_FLUSH_INTERVAL_MS: Final[int] = 5_000

# SQL for creating audit table (if not exists)
CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    action VARCHAR(64) NOT NULL,
    principal_id VARCHAR(128),
    session_id VARCHAR(128),
    resource VARCHAR(256),
    status VARCHAR(32) NOT NULL,
    detail JSONB,
    idempotency_key VARCHAR(128),
    CONSTRAINT audit_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events (timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events (action, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events (session_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_principal ON audit_events (principal_id, timestamp_ms DESC);
"""

# SQL for inserting event
INSERT_SQL: Final[str] = """
INSERT INTO audit_events
    (event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail)
VALUES
    (:event_id, :timestamp_ms, :action, :principal_id, :session_id, :resource, :status, :detail)
ON CONFLICT (event_id) DO NOTHING
"""

# SQL for inserting with idempotency key
INSERT_ONCE_SQL: Final[str] = """
INSERT INTO audit_events
    (event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail, idempotency_key)
VALUES
    (:event_id, :timestamp_ms, :action, :principal_id, :session_id, :resource, :status, :detail, :key)
ON CONFLICT (idempotency_key) DO NOTHING
"""

# SQL for tail query
TAIL_SQL: Final[str] = """
SELECT event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail
FROM audit_events
WHERE (:action IS NULL OR action = :action)
ORDER BY timestamp_ms DESC
LIMIT :limit
"""

# SQL for filtered query
QUERY_SQL: Final[str] = """
SELECT event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail
FROM audit_events
WHERE (:action IS NULL OR action = :action)
  AND (:principal_id IS NULL OR principal_id = :principal_id)
  AND (:session_id IS NULL OR session_id = :session_id)
  AND (:since_ms IS NULL OR timestamp_ms >= :since_ms)
  AND (:until_ms IS NULL OR timestamp_ms <= :until_ms)
ORDER BY timestamp_ms DESC
LIMIT :limit
OFFSET :offset
"""


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


