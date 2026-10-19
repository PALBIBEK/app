"""Dict ↔ dataclass transformations for session types.

Provides serialization helpers for API responses and storage.
All functions are pure and stateless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.session.core.types import (
        ChildRow,
        JobRow,
        ResumeDecision,
        SessionInfo,
    )

__all__ = [
    "session_info_to_dict",
    "resume_decision_to_dict",
    "child_row_to_dict",
    "job_row_to_dict",
]


# =============================================================================
# SessionInfo Converters
# =============================================================================


def session_info_to_dict(info: SessionInfo) -> dict[str, Any]:
    """Convert SessionInfo to dictionary for API responses.

    Args:
        info: SessionInfo instance

    Returns:
        Dictionary with all session metadata
    """
    return {
        "session_id": info.session_id,
        "tenant_id": info.tenant_id,
        "harness_version": info.harness_version,
        "runtime": info.runtime,
        "status": info.status.value,
        "mode": info.mode.value,
        "is_busy": info.is_busy,
        "created_at": info.created_at.isoformat() if info.created_at else None,
        "composition_id": info.composition_id,
        "sandbox_id": info.sandbox_id,
        "parent_session_id": info.parent_session_id,
    }


# =============================================================================
# Recovery Converters
# =============================================================================


def resume_decision_to_dict(decision: ResumeDecision) -> dict[str, Any]:
    """Convert ResumeDecision to dictionary for API/logging.

    Args:
        decision: ResumeDecision instance

    Returns:
        Dictionary with decision details
    """
    return {
        "state": decision.state.value,
        "harness_version": decision.harness.version if decision.harness else None,
        "unpaired_tool_call_ids": list(decision.unpaired_tool_call_ids),
        "unpaired_compaction_ids": list(decision.unpaired_compaction_ids),
        "applied_actions": [action.value for action in decision.applied_actions],
    }


# =============================================================================
# Tree Converters
# =============================================================================


def child_row_to_dict(row: ChildRow) -> dict[str, Any]:
    """Convert ChildRow to dictionary for API responses.

    Args:
        row: ChildRow instance

    Returns:
        Dictionary with child session info
    """
    return {
        "id": row.id,
        "mode": row.mode,
        "provider": row.provider,
        "parent_id": row.parent_id,
        "run_id": row.run_id,
        "label": row.label,
        "activity": row.activity.value,
        "depth": row.depth,
    }


def job_row_to_dict(row: JobRow) -> dict[str, Any]:
    """Convert JobRow to dictionary for API responses.

    Args:
        row: JobRow instance

    Returns:
        Dictionary with job info
    """
    return {
        "job_id": row.job_id,
        "kind": row.kind,
        "activity": row.activity.value,
        "stop_reason": row.stop_reason,
        "output": row.output,
    }
