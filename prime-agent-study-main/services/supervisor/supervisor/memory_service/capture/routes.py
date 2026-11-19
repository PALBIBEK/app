"""The capture queue's HTTP API (G3), behind the service guard like every other route.

A job, a lease or a session belongs to one tenant: a body naming another tenant is refused
(403), and another tenant's job or session is not found (404). ``GET /v1/capture/tenants``
is the one cross-tenant read: which tenants have work to claim, in fair order, and nothing
of it. Service credentials are minted only by the supervisor (``seams/services``), which
already acts for every tenant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from supervisor.memory_service.auth import enforce_item_tenant, enforce_scope_tenant
from supervisor.memory_service.capture.errors import (
    CaptureError,
    CaptureInvalid,
    StaleResolution,
    UnknownCaptureSession,
)
from supervisor.memory_service.capture.queue import (
    MAX_LEASE_S,
    CaptureCursors,
    CaptureJob,
    CaptureLease,
    CaptureQueue,
    CaptureWorkState,
)
from supervisor.memory_service.notebooks.errors import NotebookNotFound
from supervisor.memory_service.notebooks.wire import provenance_out

router = APIRouter(prefix="/v1/capture", tags=["capture"])

FLUSH_TIMEOUT_S = 240.0
"""Grok's ``FLUSH_TIMEOUT`` (four minutes): the longest one barrier call waits."""


class EnqueueIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    notebook_id: str = Field(min_length=1, max_length=512)
    run_ids: list[str] = Field(min_length=1, max_length=200)


class ClaimIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=256)
    owner: str = Field(min_length=1, max_length=128)
    lease_s: float = Field(ge=1, le=MAX_LEASE_S)
    session_id: str | None = None
    job_id: str | None = None
    pending_grace_s: float = Field(default=0, ge=0, le=3600)


class JobIn(BaseModel):
    job_id: str
    tenant_id: str
    session_id: str
    notebook_id: str
    run_ids: list[str]
    from_turn: int
    through_turn: int
    attempt: int


class LeaseIn(BaseModel):
    job: JobIn
    owner: str
    expires_at: int


class CommitIn(BaseModel):
    lease: LeaseIn
    outcome: dict[str, Any]
    resolved_at: str | None = None


class FailIn(BaseModel):
    lease: LeaseIn
    error: str = Field(max_length=16_384)
    terminal: bool = False


class ReleaseIn(BaseModel):
    lease: LeaseIn
    error: str = Field(max_length=16_384)
    stale: bool = False


class RenewIn(BaseModel):
    lease: LeaseIn
    lease_s: float = Field(ge=1, le=MAX_LEASE_S)


class FlushIn(BaseModel):
    tenant_id: str
    through_turn: int | None = Field(default=None, ge=0)
    timeout_s: float = Field(default=FLUSH_TIMEOUT_S, ge=0, le=FLUSH_TIMEOUT_S)


def _queue(request: Request) -> CaptureQueue:
    return request.app.state.memory.capture  # type: ignore[no-any-return]


def _error(exc: CaptureError) -> HTTPException:
    detail: dict[str, Any] = {"code": type(exc).__name__, "message": str(exc)}
    status = 409
    if isinstance(exc, CaptureInvalid):
        status = 422
    elif isinstance(exc, UnknownCaptureSession):
        status = 404
        detail["session_id"] = exc.session_id
    elif isinstance(exc, StaleResolution):
        detail["expected"], detail["actual"] = exc.expected, exc.actual
    return HTTPException(status_code=status, detail=detail)


def job_out(job: CaptureJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "tenant_id": job.tenant_id,
        "session_id": job.session_id,
        "notebook_id": job.notebook_id,
        "run_ids": list(job.run_ids),
        "from_turn": job.from_turn,
        "through_turn": job.through_turn,
        "attempt": job.attempt,
    }


def lease_out(lease: CaptureLease) -> dict[str, Any]:
    return {"job": job_out(lease.job), "owner": lease.owner, "expires_at": lease.expires_at}


def _lease_in(request: Request, body: LeaseIn) -> CaptureLease:
    enforce_scope_tenant(request, body.job.tenant_id)
    job = body.job
    return CaptureLease(
        CaptureJob(
            job.job_id, job.tenant_id, job.session_id, job.notebook_id, tuple(job.run_ids),
            job.from_turn, job.through_turn, job.attempt,
        ),
        body.owner,
        body.expires_at,
    )


def _cursors_out(cursors: CaptureCursors) -> dict[str, int]:
    return {"requested": cursors.requested, "captured": cursors.captured, "indexed": cursors.indexed}


def _work_out(work: CaptureWorkState) -> dict[str, Any]:
    return {
        "pending": work.pending, "running": work.running, "failed": work.failed,
        "terminal": work.terminal, "last_error": work.last_error,
    }


@router.post("/jobs")
async def enqueue(body: EnqueueIn, request: Request) -> dict[str, Any]:
    enforce_scope_tenant(request, body.tenant_id)
    try:
        notebook = await request.app.state.memory.notebooks.store.get(body.notebook_id)
    except NotebookNotFound as exc:
        raise HTTPException(404, {"code": "NotebookNotFound", "message": str(exc)}) from exc
    enforce_item_tenant(request, notebook.tenant_id)
    if notebook.tenant_id != body.tenant_id:
        raise HTTPException(404, {"code": "NotebookNotFound", "message": "not found"})
    try:
        job = await _queue(request).enqueue(
            tenant_id=body.tenant_id, session_id=body.session_id, notebook_id=body.notebook_id, run_ids=body.run_ids
        )
    except CaptureError as exc:
        raise _error(exc) from exc
    return job_out(job)


@router.get("/tenants")
async def claimable_tenants(
    request: Request, limit: int = Query(32, ge=1, le=256), pending_grace_s: float = Query(0, ge=0, le=3600)
) -> dict[str, list[str]]:
    tenants = await _queue(request).claimable_tenants(limit=limit, pending_grace_ms=int(pending_grace_s * 1000))
    return {"tenants": tenants}


@router.post("/jobs:claim")
async def claim(body: ClaimIn, request: Request) -> dict[str, Any]:
    enforce_scope_tenant(request, body.tenant_id)
    try:
        lease = await _queue(request).claim(
            tenant_id=body.tenant_id, owner=body.owner, lease_s=body.lease_s, session_id=body.session_id,
            job_id=body.job_id, pending_grace_ms=int(body.pending_grace_s * 1000),
        )
    except CaptureError as exc:
        raise _error(exc) from exc
    return {"lease": None if lease is None else lease_out(lease)}


@router.post("/jobs/{job_id}:commit")
async def commit(job_id: str, body: CommitIn, request: Request) -> dict[str, Any]:
    lease = _checked(job_id, request, body.lease)
    try:
        result = await _queue(request).commit(lease, body.outcome, resolved_at=body.resolved_at)
    except CaptureError as exc:
        raise _error(exc) from exc
    return {"outcome_hash": result.outcome_hash, "was_already_committed": result.was_already_committed}


@router.post("/jobs/{job_id}:renew")
async def renew(job_id: str, body: RenewIn, request: Request) -> dict[str, Any]:
    lease = _checked(job_id, request, body.lease)
    try:
        renewed = await _queue(request).renew(lease, body.lease_s)
    except CaptureError as exc:
        raise _error(exc) from exc
    return {"lease": lease_out(renewed)}


@router.post("/jobs/{job_id}:fail", status_code=204)
async def fail(job_id: str, body: FailIn, request: Request) -> None:
    lease = _checked(job_id, request, body.lease)
    queue = _queue(request)
    try:
        await (queue.fail_terminal if body.terminal else queue.fail_retryable)(lease, body.error)
    except CaptureError as exc:
        raise _error(exc) from exc


@router.post("/jobs/{job_id}:release", status_code=204)
async def release(job_id: str, body: ReleaseIn, request: Request) -> None:
    lease = _checked(job_id, request, body.lease)
    try:
        await _queue(request).release_retryable(lease, body.error, stale=body.stale)
    except CaptureError as exc:
        raise _error(exc) from exc


@router.get("/sessions/{session_id}")
async def session_state(session_id: str, request: Request, tenant_id: str = Query(...)) -> dict[str, Any]:
    enforce_scope_tenant(request, tenant_id)
    queue = _queue(request)
    try:
        cursors = await queue.cursors(tenant_id=tenant_id, session_id=session_id)
    except CaptureError as exc:
        raise _error(exc) from exc
    work = await queue.work_state(tenant_id=tenant_id, session_id=session_id, through_turn=cursors.requested)
    return {"cursors": _cursors_out(cursors), "work": _work_out(work)}


@router.post("/sessions/{session_id}:flush")
async def flush(session_id: str, body: FlushIn, request: Request) -> dict[str, Any]:
    """Grok's ``/flush`` barrier: until the session is indexed through ``through_turn``
    (its latest queued turn when absent), or ``timeout_s``."""
    enforce_scope_tenant(request, body.tenant_id)
    try:
        result, cursors, work = await _queue(request).flush(
            tenant_id=body.tenant_id, session_id=session_id, through_turn=body.through_turn, timeout_s=body.timeout_s
        )
    except CaptureError as exc:
        raise _error(exc) from exc
    return {"result": result, "cursors": _cursors_out(cursors), "work": _work_out(work)}


@router.get("/notebooks/{notebook_id}")
async def notebook_view(notebook_id: str, request: Request) -> dict[str, Any]:
    """What resolution compares a burst with, beyond the facts: the statements committed
    and not landed yet (``CaptureQueue.view``), and the revision read first (S3)."""
    store = request.app.state.memory.notebooks.store
    try:
        notebook = await store.get(notebook_id)
    except NotebookNotFound as exc:
        raise HTTPException(404, {"code": "NotebookNotFound", "message": str(exc)}) from exc
    enforce_item_tenant(request, notebook.tenant_id)
    revision, statements = await _queue(request).view(notebook_id)
    return {
        "revision": revision,
        "queued": [{"statement": text, "provenance": provenance_out(p)} for text, p in statements],
    }


def _checked(job_id: str, request: Request, body: LeaseIn) -> CaptureLease:
    if body.job.job_id != job_id:
        raise HTTPException(422, {"code": "CaptureInvalid", "message": "the lease names another job"})
    return _lease_in(request, body)
