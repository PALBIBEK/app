"""HTTP routes for notebooks: the memory service's one store of long-term facts.

Behind the service guard like every other route. A notebook's tenant must be the
caller's: a body that names another tenant is refused (403), and a notebook of
another tenant is not found (404), so its existence does not leak. An error's
``code`` is its class name, with the details the supervisor rebuilds it from.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from supervisor.memory_service.auth import enforce_item_tenant, enforce_scope_tenant
from supervisor.memory_service.notebooks import wire
from supervisor.memory_service.notebooks.errors import (
    MemoryFactsMissing,
    NotebookError,
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.ops import NotebookOps
from supervisor.memory_service.notebooks.types import Notebook

router = APIRouter(prefix="/v1/notebooks", tags=["notebooks"])


class NotebookKey(BaseModel):
    tenant_id: str
    owner: str
    agent_pin: str
    name: str
    agent: str | None = None
    label: str | None = None


class ArchiveIn(BaseModel):
    archived: bool


class TidiedIn(BaseModel):
    at: int


class CaptureIn(BaseModel):
    facts: list[str]
    author: str | None = None
    context: dict[str, Any] | None = None


class EditIn(BaseModel):
    edits: list[dict[str, Any]]
    author: str | None = None
    context: dict[str, Any] | None = None


class DeleteIn(BaseModel):
    ids: list[str] = Field(default_factory=list)
    author: str | None = None


class AuthorIn(BaseModel):
    author: str | None = None


class RestoreIn(BaseModel):
    revision: int
    author: str | None = None


def _ops(request: Request) -> NotebookOps:
    ops: NotebookOps = request.app.state.memory.notebooks
    return ops


def _error(exc: NotebookError) -> HTTPException:
    detail: dict[str, Any] = {"code": type(exc).__name__, "message": str(exc)}
    status = 409
    if isinstance(exc, NotebookNotFound):
        status, detail["ref"] = 404, exc.ref
    elif isinstance(exc, NotebookExists):
        detail["name"] = exc.name
    elif isinstance(exc, MemoryFactsMissing):
        status, detail["ids"] = 404, list(exc.ids)
    elif isinstance(exc, RevisionConflict):
        detail["expected"], detail["actual"] = exc.expected, exc.actual
    return HTTPException(status_code=status, detail=detail)


async def _owned(request: Request, notebook_id: str) -> Notebook:
    """The notebook, if it is the caller's tenant's; otherwise not found."""
    try:
        notebook = await _ops(request).store.get(notebook_id)
    except NotebookNotFound as exc:
        raise _error(exc) from exc
    enforce_item_tenant(request, notebook.tenant_id)
    return notebook


@router.post("", status_code=201)
async def create_notebook(body: NotebookKey, request: Request) -> dict[str, Any]:
    enforce_scope_tenant(request, body.tenant_id)
    try:
        notebook = await _ops(request).store.create(
            tenant_id=body.tenant_id, owner=body.owner, agent_pin=body.agent_pin, name=body.name,
            label=body.label, agent=body.agent,
        )
    except NotebookError as exc:
        raise _error(exc) from exc
    return wire.notebook_out(notebook)


@router.post(":ensure")
async def ensure_notebook(body: NotebookKey, request: Request) -> dict[str, Any]:
    enforce_scope_tenant(request, body.tenant_id)
    notebook = await _ops(request).store.ensure(
        tenant_id=body.tenant_id, owner=body.owner, agent_pin=body.agent_pin, name=body.name,
        agent=body.agent,
    )
    return wire.notebook_out(notebook)


@router.get("")
async def list_notebooks(
    request: Request,
    tenant_id: str = Query(...),
    owner: str = Query(...),
    agent_pin: str = Query(...),
    archived: bool = Query(default=False),
) -> list[dict[str, Any]]:
    enforce_scope_tenant(request, tenant_id)
    notebooks = await _ops(request).store.list_notebooks(
        tenant_id=tenant_id, owner=owner, agent_pin=agent_pin, archived=archived
    )
    return [wire.notebook_out(n) for n in notebooks]


@router.get("/{notebook_id}")
async def get_notebook(notebook_id: str, request: Request) -> dict[str, Any]:
    return wire.notebook_out(await _owned(request, notebook_id))


@router.post("/{notebook_id}/archive")
async def archive_notebook(notebook_id: str, body: ArchiveIn, request: Request) -> dict[str, Any]:
    await _owned(request, notebook_id)
    return wire.notebook_out(await _ops(request).store.set_archived(notebook_id, body.archived))


@router.post("/{notebook_id}/tidied", status_code=204)
async def mark_tidied(notebook_id: str, body: TidiedIn, request: Request) -> None:
    await _owned(request, notebook_id)
    await _ops(request).store.mark_tidied(notebook_id, body.at)


@router.get("/{notebook_id}/stats")
async def notebook_stats(notebook_id: str, request: Request) -> dict[str, Any]:
    await _owned(request, notebook_id)
    return wire.stats_out(await _ops(request).store.stats(notebook_id))


@router.get("/{notebook_id}/facts")
async def list_facts(
    notebook_id: str, request: Request, at_revision: int | None = Query(default=None)
) -> list[dict[str, Any]]:
    """The live facts, or (``at_revision``) the facts as they were then."""
    await _owned(request, notebook_id)
    ops = _ops(request)
    facts = await (ops.store.facts(notebook_id) if at_revision is None else ops.facts_at(notebook_id, at_revision))
    return [wire.fact_out(f) for f in facts]


@router.get("/{notebook_id}/revisions")
async def list_revisions(
    notebook_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100_000, ge=1, le=100_000),
) -> list[dict[str, Any]]:
    await _owned(request, notebook_id)
    revisions = await _ops(request).store.revisions(notebook_id, after=after, limit=limit)
    return [wire.revision_out(r) for r in revisions]


@router.post("/{notebook_id}/facts:capture")
async def capture_facts(notebook_id: str, body: CaptureIn, request: Request) -> dict[str, int]:
    await _owned(request, notebook_id)
    try:
        added = await _ops(request).capture(
            notebook_id, body.facts, author=body.author, context=wire.context_in(body.context)
        )
    except NotebookError as exc:
        raise _error(exc) from exc
    return {"added": added}


@router.post("/{notebook_id}/facts:edit", status_code=204)
async def edit_facts(notebook_id: str, body: EditIn, request: Request) -> None:
    await _owned(request, notebook_id)
    try:
        await _ops(request).edit(
            notebook_id, [wire.edit_in(e) for e in body.edits], author=body.author,
            context=wire.context_in(body.context),
        )
    except NotebookError as exc:
        raise _error(exc) from exc


@router.post("/{notebook_id}/facts:delete", status_code=204)
async def delete_facts(notebook_id: str, body: DeleteIn, request: Request) -> None:
    await _owned(request, notebook_id)
    try:
        await _ops(request).delete(notebook_id, body.ids, author=body.author)
    except NotebookError as exc:
        raise _error(exc) from exc


@router.post("/{notebook_id}/facts:clear")
async def clear_facts(notebook_id: str, body: AuthorIn, request: Request) -> dict[str, int]:
    await _owned(request, notebook_id)
    return {"cleared": await _ops(request).clear(notebook_id, author=body.author)}


@router.post("/{notebook_id}/facts:restore", status_code=204)
async def restore_facts(notebook_id: str, body: RestoreIn, request: Request) -> None:
    await _owned(request, notebook_id)
    try:
        await _ops(request).restore(notebook_id, body.revision, author=body.author)
    except NotebookError as exc:
        raise _error(exc) from exc
