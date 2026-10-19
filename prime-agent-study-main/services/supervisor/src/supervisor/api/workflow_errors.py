"""HTTP mapping for session commands refused by a workflow (or never delivered)."""

from __future__ import annotations

from typing import Any, Final

from fastapi import HTTPException

from supervisor.api.catalog import _CATALOG_ERROR_STATUS
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable

# Same statuses the endpoints returned when they called SessionManager directly.
_STATUS: Final[dict[str, int]] = {
    "SessionNotFound": 404,
    "SessionClosed": 409,
    "SessionTerminated": 409,
    "SessionAwaitingApproval": 409,
    "RecoveryBlocked": 409,
    "BudgetExceeded": 429,
    "BudgetCostExceeded": 429,
    "BudgetTokensExceeded": 429,
    # A worker reports the concrete class; each quota a tenant can run out of is a 429.
    "QuotaExceeded": 429,
    "SessionQuotaExceeded": 429,
    "SandboxQuotaExceeded": 429,
    "CostQuotaExceeded": 429,
    "GrantDenied": 403,
    "NotScopeMember": 403,
    "HarnessVersionMismatch": 422,
    "UnknownHarness": 422,
    "HarnessNotOffered": 409,
    "NotOffered": 409,
    "IncompatibleHarness": 503,
    "ApprovalNotFound": 404,
    "ApprovalAlreadyResponded": 409,
    "ApprovalTimedOut": 409,
    "InvalidApprovalChoice": 422,
    "ValueError": 422,
    # Session operations (tool invoke, memory, subagents, workspace).
    "ToolInvokeNotConfigured": 409,
    "SubagentNotConfigured": 409,
    "MemoryNotConfigured": 409,
    "WorkspaceNotReadyError": 409,
    "WorkspaceUnavailable": 409,
    "ToolOutcomeUnknown": 503,
    "DepthExceeded": 400,
    "BadArgs": 400,
    "UnsupportedCapability": 400,
    "ProviderConflict": 409,
    "ProviderMissing": 404,
    "SeamError": 400,
}


def _detail(err: SessionCommandError) -> Any:
    message = str(err)
    match err.kind:
        case "RecoveryBlocked":
            tools = err.details.get("tool_call_ids", [])
            return {
                "reason": "recovery_blocked",
                "state": err.details.get("state", ""),
                "tool_call_ids": tools,
                "message": f"recovery blocked by unpaired tool call(s): {', '.join(tools)}",
            }
        case "BudgetExceeded" | "BudgetCostExceeded" | "BudgetTokensExceeded":
            return {"reason": "budget_exceeded", "message": message}
        case "QuotaExceeded" | "SessionQuotaExceeded" | "SandboxQuotaExceeded" | "CostQuotaExceeded":
            return {"reason": "quota_exceeded", "message": message}
        case "HarnessVersionMismatch":
            return {"code": err.details.get("code", err.kind), "message": message}
        case "SessionTerminated" | "SessionClosed":
            return "session terminated"
        case "DepthExceeded":
            return {"code": err.details.get("code", "DEPTH_EXCEEDED"), "message": message}
        case "ToolOutcomeUnknown":
            return {"reason": "tool_outcome_unknown", "message": message}
        case _:
            return message


def to_http(err: SessionCommandError | TemporalUnavailable) -> HTTPException:
    if isinstance(err, TemporalUnavailable):
        return HTTPException(
            status_code=503, detail={"reason": "temporal_unavailable", "message": str(err)}
        )
    status = _STATUS.get(err.kind) or _CATALOG_ERROR_STATUS.get(err.details.get("code", ""), 503)
    return HTTPException(status_code=status, detail=_detail(err))
