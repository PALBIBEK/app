"""Storage layer for session persistence and caching.

This module provides:
- SessionRegistry: In-memory session→runtime mapping with metrics
- ToolOutbox: Idempotent tool invocation tracking
- Converters: Dict ↔ dataclass transformations
"""

from __future__ import annotations

from supervisor.session.storage.registry import SessionRegistry
from supervisor.session.storage.outbox import (
    ToolOutbox,
    idempotency_key,
    result_to_wire,
    wire_to_result,
)
from supervisor.session.storage.converters import (
    session_info_to_dict,
    resume_decision_to_dict,
    child_row_to_dict,
    job_row_to_dict,
)

__all__ = [
    # Registry
    "SessionRegistry",
    # Outbox
    "ToolOutbox",
    "idempotency_key",
    "result_to_wire",
    "wire_to_result",
    # Converters
    "session_info_to_dict",
    "resume_decision_to_dict",
    "child_row_to_dict",
    "job_row_to_dict",
]
