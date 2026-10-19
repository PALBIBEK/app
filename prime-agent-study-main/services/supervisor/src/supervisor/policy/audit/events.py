"""Audit event helpers — creation and formatting.

Provides helper functions for creating and formatting audit events.
"""

from __future__ import annotations

import time
from typing import Any, Literal
from uuid import uuid4

from supervisor.policy.types import AuditAction, AuditEvent

__all__ = [
    "create_event",
    "event_to_dict",
    "format_event",
]


def create_event(
    action: AuditAction,
    principal_id: str,
    status: Literal["success", "failure", "blocked"],
    *,
    session_id: str | None = None,
    resource: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Create audit event with auto-generated ID and timestamp.

    Args:
        action: Action being audited
        principal_id: Principal performing action
        status: Outcome status
        session_id: Optional session context
        resource: Optional resource being accessed
        detail: Optional additional detail

    Returns:
        AuditEvent instance
    """
    return AuditEvent(
        event_id=uuid4().hex,
        timestamp_ms=int(time.time() * 1000),
        action=action,
        principal_id=principal_id,
        session_id=session_id,
        resource=resource,
        status=status,
        detail=detail,
    )


def format_event(event: AuditEvent) -> str:
    """Format audit event as human-readable string.

    Args:
        event: Event to format

    Returns:
        Formatted string
    """
    parts = [
        f"[{event.timestamp_ms}]",
        f"[{event.action.value}]",
        f"principal={event.principal_id}",
        f"status={event.status}",
    ]

    if event.session_id:
        parts.append(f"session={event.session_id}")

    if event.resource:
        parts.append(f"resource={event.resource}")

    if event.detail:
        detail_str = " ".join(f"{k}={v}" for k, v in event.detail.items())
        parts.append(f"detail={{{detail_str}}}")

    return " ".join(parts)


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    """Convert event to dictionary for JSON serialization.

    Args:
        event: Event to convert

    Returns:
        Dictionary representation
    """
    return {
        "event_id": event.event_id,
        "timestamp_ms": event.timestamp_ms,
        "action": event.action.value,
        "principal_id": event.principal_id,
        "session_id": event.session_id,
        "resource": event.resource,
        "status": event.status,
        "detail": event.detail,
    }
