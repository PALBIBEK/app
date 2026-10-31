"""Session operations from HTTP: through the session's workflow, never this process.

The session's runtime and sandbox live on the worker holding it;
running an operation here would resume a second copy of the session. Only
in-process mode (tests, no workflows) runs it locally, via the same dispatch.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

from supervisor.api import deps
from supervisor.api.workflow_errors import to_http
from supervisor.session.dispatch import run_operation
from supervisor.temporal.contracts import Op, SessionOp
from supervisor.temporal.domain import domain_kind, error_details
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable


def domain_http_error(exc: Exception) -> HTTPException | None:
    """The HTTP error for a domain exception raised in this process, else None."""
    if (kind := domain_kind(exc)) is None:
        return None
    return to_http(SessionCommandError(kind, str(exc), error_details(exc)))


async def run_op(
    request: Request,
    session_id: str,
    tenant_id: str,
    kind: Op,
    args: dict[str, Any],
    *,
    op_id: str | None = None,
) -> dict[str, Any] | None:
    """The operation's response body; domain errors become their usual HTTP status."""
    op = SessionOp(session_id, op_id or uuid4().hex, kind, tenant_id, args)
    if (workflows := deps.get_workflows(request)) is not None:
        try:
            return await workflows.op(op)
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
    manager = deps.get_manager(request)
    try:
        return await run_operation(manager, op)
    except Exception as exc:
        if (http := domain_http_error(exc)) is None:
            raise
        raise http from exc
