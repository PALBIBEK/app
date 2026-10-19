"""FastAPI surface for durable Memory Service (Tier 1)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from memory_service.auth import (
    caller_tenant,
    enforce_item_tenant,
    enforce_scope_tenant,
    resolve_http_auth,
)
from memory_service.rate_limit import TokenBucketLimiter, is_mutating_method, rate_limit_key
from memory_service.service import MemoryService
from memory_service.types import (
    FormatUnsupported,
    LeaseHeld,
    LogicalSession,
    Scope,
    ScopeRequired,
    SessionCorrupt,
    SessionNotFound,
    StaleFence,
)
from memory_service.tenant import TenantMismatch, TenantRequired


class ScopeIn(BaseModel):
    tenant: str
    subject: str
    namespace: str


class CreateSessionIn(BaseModel):
    scope: ScopeIn
    session_id: str | None = None


class BufferEventIn(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    time: int | None = None


class BufferEventsIn(BaseModel):
    events: list[BufferEventIn]


class FlushIn(BaseModel):
    fence: int | None = None


class LeaseIn(BaseModel):
    owner: str


class ForkIn(BaseModel):
    at_seq: int | None = None


class OutboxExecuteIn(BaseModel):
    """Test/demo: echo executor returns payload back. Real executors are in-process."""

    mode: str = "echo"


class WriteItemIn(BaseModel):
    scope: ScopeIn
    content: str
    confidence: float = 0.8
    source_session: str | None = None
    source_seq: int | None = None


class SupersedeIn(BaseModel):
    scope: ScopeIn
    content: str
    confidence: float = 0.8
    source_session: str | None = None
    source_seq: int | None = None


class RecallIn(BaseModel):
    scope: ScopeIn
    query: str
    max_tokens: int = 512
    log_session_id: str | None = None


class ForgetIn(BaseModel):
    scope: ScopeIn
    item_id: str | None = None
    source_session: str | None = None
    all_in_scope: bool = False
    actor: str = "system"
    reason: str = "api"


class EraseSubjectIn(BaseModel):
    tenant: str
    subject: str
    actor: str
    reason: str


def _svc(request: Request) -> MemoryService:
    return request.app.state.memory


def _scope(request: Request, scope: ScopeIn) -> Scope:
    """The request's scope, refused (403) unless it is the verified caller's tenant."""
    enforce_scope_tenant(request, scope.tenant)
    return Scope(tenant=scope.tenant, subject=scope.subject, namespace=scope.namespace)


async def _own_session(request: Request, session_id: str) -> None:
    """A session addressed by id belongs to the caller's tenant, or it is not found (404)."""
    tenant = await _svc(request).session_tenant(session_id)
    if tenant is None:
        raise HTTPException(
            status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": session_id}
        )
    enforce_item_tenant(request, tenant)


PUBLIC_PATHS = frozenset({"/v1/healthz", "/v1/readyz"})
"""Process and readiness probes: answered without credentials (compose and k8s health checks)."""


async def _guard(request: Request) -> None:
    """Every other route: a verified caller (``auth.resolve_http_auth``), and mutations rate-limited.

    The bucket is per verified tenant (else per client address); an empty bucket is a
    429 with ``Retry-After``.
    """
    if request.url.path in PUBLIC_PATHS:
        return
    resolve_http_auth(request)
    if not is_mutating_method(request.method):
        return
    key = rate_limit_key(
        tenant_id=caller_tenant(request) or request.headers.get("x-tenant-id"),
        client_ip=request.client.host if request.client else None,
    )
    if not request.app.state.rate_limiter.allow(key):
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMITED", "message": "too many mutating requests"},
            headers={"Retry-After": "1"},
        )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):  # already an answer (a tenant check, say): keep it
        return exc
    if isinstance(exc, SessionNotFound):
        return HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, (SessionCorrupt, FormatUnsupported)):
        return HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, (StaleFence, LeaseHeld)):
        return HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, (ScopeRequired, TenantRequired, ValueError)) and (
        "SCOPE" in str(exc) or "TENANT" in str(exc) or "actor" in str(exc)
    ):
        return HTTPException(status_code=422, detail={"code": "SCOPE_REQUIRED", "message": str(exc)})
    if isinstance(exc, TenantMismatch):
        return HTTPException(status_code=403, detail={"code": exc.code, "message": str(exc)})
    return HTTPException(status_code=500, detail={"code": "INTERNAL", "message": str(exc)})


def create_app(*, database_url: str = "sqlite+aiosqlite:///:memory:") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        existing = getattr(app.state, "memory", None)
        if existing is None:
            svc = MemoryService(database_url=database_url)
            await svc.startup()
            app.state.memory = svc
            owned = True
        else:
            owned = False
        yield
        if owned:
            await app.state.memory.aclose()

    app = FastAPI(
        title="yal-memory-service",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(_guard)],
    )
    app.state.rate_limiter = TokenBucketLimiter()

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/readyz", response_model=None)
    async def readyz(request: Request) -> dict[str, str] | JSONResponse:
        """Ready when the database answers; the process probe is /v1/healthz."""
        if await _svc(request).ping_db():
            return {"status": "ready"}
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "database"})

    @app.post("/v1/sessions", status_code=201)
    async def create_session(body: CreateSessionIn, request: Request) -> dict[str, Any]:
        try:
            scope = _scope(request, body.scope)
            handle = await _svc(request).create_session(scope, session_id=body.session_id)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {
            "id": handle.id,
            "scope": {
                "tenant": handle.scope.tenant,
                "subject": handle.scope.subject,
                "namespace": handle.scope.namespace,
            },
            "format_version": handle.format_version,
        }

    @app.post("/v1/sessions/{session_id}/events:buffer")
    async def buffer_events(
        session_id: str, body: BufferEventsIn, request: Request
    ) -> dict[str, Any]:
        await _own_session(request, session_id)
        svc = _svc(request)
        seqs: list[int] = []
        for ev in body.events:
            written = svc.append(session_id, ev.type, ev.data, time_ms=ev.time)
            seqs.append(written.seq)
        return {"buffered": len(seqs), "seqs": seqs, "pending": svc.pending_count(session_id)}

    @app.post("/v1/sessions/{session_id}/flush")
    async def flush(session_id: str, body: FlushIn, request: Request) -> dict[str, Any]:
        await _own_session(request, session_id)
        try:
            last = await _svc(request).flush(session_id, fence=body.fence)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"last_seq": last}

    @app.get("/v1/sessions/{session_id}")
    async def load_session(session_id: str, request: Request) -> dict[str, Any]:
        await _own_session(request, session_id)
        try:
            logical: LogicalSession = await _svc(request).load(session_id)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {
            "id": logical.handle.id,
            "status": logical.status,
            "repaired": logical.repaired,
            "format_version": logical.handle.format_version,
            "scope": {
                "tenant": logical.handle.scope.tenant,
                "subject": logical.handle.scope.subject,
                "namespace": logical.handle.scope.namespace,
            },
            "parent_id": logical.parent_id,
            "forked_at_seq": logical.forked_at_seq,
            "created_at": logical.created_at,
            "events": [
                {"type": e.type, "seq": e.seq, "time": e.time, "data": e.data}
                for e in logical.events
            ],
        }

    @app.post("/v1/sessions/{session_id}/fork", status_code=201)
    async def fork(session_id: str, body: ForkIn, request: Request) -> dict[str, Any]:
        await _own_session(request, session_id)
        try:
            handle = await _svc(request).fork(session_id, at_seq=body.at_seq)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"id": handle.id, "format_version": handle.format_version}

    @app.get("/v1/sessions")
    async def list_sessions(
        request: Request,
        tenant: str = Query(...),
        subject: str = Query(...),
        namespace: str = Query(...),
    ) -> dict[str, Any]:
        try:
            enforce_scope_tenant(request, tenant)
            scope = Scope(tenant=tenant, subject=subject, namespace=namespace)
            items = await _svc(request).list_sessions(scope)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {
            "sessions": [
                {
                    "id": s.id,
                    "status": s.status,
                    "created_at": s.created_at,
                    "event_count": s.event_count,
                }
                for s in items
            ]
        }

    @app.post("/v1/sessions/{session_id}/lease")
    async def lease(session_id: str, body: LeaseIn, request: Request) -> dict[str, Any]:
        await _own_session(request, session_id)
        try:
            fence = await _svc(request).acquire_lease(session_id, body.owner)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"fence": fence, "owner": body.owner}

    @app.post("/v1/sessions/{session_id}/outbox:execute")
    async def outbox_execute(
        session_id: str, body: OutboxExecuteIn, request: Request
    ) -> dict[str, Any]:
        await _own_session(request, session_id)
        def echo(payload: dict[str, Any]) -> dict[str, Any]:
            return {"echo": payload, "mode": body.mode}

        try:
            results = await _svc(request).execute_outbox(session_id, echo)
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"results": results}

    # --- Tier 2 ---

    @app.post("/v1/memory/items", status_code=201)
    async def write_item(payload: WriteItemIn, request: Request) -> dict[str, Any]:
        from memory_service.tier2_types import NewMemoryItem

        try:
            scope = _scope(request, payload.scope)
            item_id = await _svc(request).write(
                scope,
                NewMemoryItem(
                    content=payload.content,
                    confidence=payload.confidence,
                    source_session=payload.source_session,
                    source_seq=payload.source_seq,
                ),
            )
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"id": item_id}

    @app.get("/v1/memory/items/{item_id}")
    async def get_item(item_id: str, request: Request) -> dict[str, Any]:
        item = await _svc(request).get(item_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail={"code": "ITEM_NOT_FOUND", "message": item_id}
            )
        enforce_item_tenant(request, item.scope.tenant)
        return {
            "id": item.id,
            "content": item.content,
            "confidence": item.confidence,
            "scope": {
                "tenant": item.scope.tenant,
                "subject": item.scope.subject,
                "namespace": item.scope.namespace,
            },
            "superseded_by": item.superseded_by,
            "source_session": item.source_session,
            "source_seq": item.source_seq,
        }

    @app.post("/v1/memory/items/{item_id}/supersede", status_code=201)
    async def supersede_item(
        item_id: str, payload: SupersedeIn, request: Request
    ) -> dict[str, Any]:
        from memory_service.tier2_types import NewMemoryItem

        try:
            scope = _scope(request, payload.scope)
            new_id = await _svc(request).supersede(
                item_id,
                NewMemoryItem(
                    content=payload.content,
                    confidence=payload.confidence,
                    source_session=payload.source_session,
                    source_seq=payload.source_seq,
                ),
                scope,
            )
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"id": new_id}

    @app.post("/v1/memory/recall")
    async def recall_items(payload: RecallIn, request: Request) -> dict[str, Any]:
        from memory_service.tier2_types import TokenBudget

        try:
            scope = _scope(request, payload.scope)
            result = await _svc(request).recall(
                scope,
                payload.query,
                TokenBudget(max_tokens=payload.max_tokens),
                log_session_id=payload.log_session_id,
            )
        except Exception as exc:
            raise _map_error(exc) from exc
        return {
            "item_ids": list(result.item_ids),
            "tokens_used": result.tokens_used,
            "items": [
                {"id": i.id, "content": i.content, "confidence": i.confidence}
                for i in result.items
            ],
        }

    @app.post("/v1/memory/forget")
    async def forget_items(payload: ForgetIn, request: Request) -> dict[str, Any]:
        from memory_service.tier2_types import ForgetSelector

        try:
            scope = _scope(request, payload.scope)
            n = await _svc(request).forget(
                scope,
                ForgetSelector(
                    item_id=payload.item_id,
                    source_session=payload.source_session,
                    all_in_scope=payload.all_in_scope,
                ),
                actor=payload.actor,
                reason=payload.reason,
            )
        except Exception as exc:
            raise _map_error(exc) from exc
        return {"forgotten": n}

    @app.post("/v1/memory/erase_subject")
    async def erase_subject(payload: EraseSubjectIn, request: Request) -> dict[str, Any]:
        enforce_scope_tenant(request, payload.tenant)
        try:
            result = await _svc(request).erase_subject(
                tenant=payload.tenant,
                subject=payload.subject,
                actor=payload.actor,
                reason=payload.reason,
            )
        except Exception as exc:
            raise _map_error(exc) from exc
        return result

    @app.post("/v1/sessions/{session_id}/archive")
    async def archive_session(session_id: str, request: Request) -> dict[str, Any]:
        await _own_session(request, session_id)
        try:
            return await _svc(request).archive_session(session_id)
        except Exception as exc:
            raise _map_error(exc) from exc

    @app.get("/v1/metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        return await _svc(request).metrics_snapshot()

    @app.post("/v1/memory/extraction:process")
    async def process_extraction(request: Request) -> dict[str, Any]:
        n = await _svc(request).process_extraction_jobs()
        return {"processed": n}

    return app


app = create_app()
