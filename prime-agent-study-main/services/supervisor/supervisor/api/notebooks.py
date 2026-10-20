"""Long-term memory over HTTP: a principal's notebooks for one exact agent version.

Every route acts as the calling principal, through the same disclosure view the
agent uses: a notebook of someone else's is "not found", facts learned where the
caller no longer has access are not shown, and writes are classified.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from supervisor.api import deps
from supervisor.api.schemas import (
    FactOut,
    FactsAdd,
    FactsAdded,
    FactsDelete,
    FactsEdit,
    FactsRemoved,
    MemoryHit,
    MemorySearch,
    NotebookCreate,
    NotebookOut,
    NotebookPatch,
    NotebookRestore,
    RevisionOut,
)
from supervisor.memory.cross import search_across
from supervisor.memory.errors import (
    MemoryDisclosureDenied,
    MemoryFactsMissing,
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory.platform import MemoryPlatform
from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory.types import Edit, Fact, Notebook
from supervisor.seams.services import Principal

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _platform(request: Request) -> MemoryPlatform:
    platform = getattr(deps._state(request), "memory", None)
    if platform is None:
        raise HTTPException(status_code=503, detail="memory is not configured")
    return platform


@asynccontextmanager
async def _errors() -> AsyncIterator[None]:
    try:
        yield
    except NotebookNotFound as exc:
        raise HTTPException(status_code=404, detail="notebook not found") from exc
    except MemoryFactsMissing as exc:
        raise HTTPException(
            status_code=404, detail={"message": str(exc), "ids": list(exc.ids)}
        ) from exc
    except NotebookExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemoryDisclosureDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _owned(platform: MemoryPlatform, notebook_id: str, principal: Principal) -> Notebook:
    """The notebook, if it is the caller's; anyone else's is indistinguishable from none."""
    async with _errors():
        notebook = await platform.store.get(notebook_id)
    if notebook.owner != principal.id or notebook.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="notebook not found")
    return notebook


def _view(platform: MemoryPlatform, principal: Principal) -> MemoryService:
    return platform.private(principal.id)


async def _out(platform: MemoryPlatform, notebook: Notebook) -> NotebookOut:
    stats = await platform.store.stats(notebook.id)
    return NotebookOut(
        id=notebook.id,
        agent_pin=notebook.agent_pin,
        name=notebook.name,
        label=notebook.label,
        revision=notebook.revision,
        created_at=notebook.created_at,
        archived=notebook.archived_at is not None,
        facts=stats.facts,
        bytes=stats.bytes,
        updated_at=stats.updated_at,
    )


def _fact(fact: Fact) -> FactOut:
    return FactOut(
        id=fact.id,
        text=fact.text,
        sensitivity=fact.provenance.sensitivity.value,
        sources=list(fact.provenance.sources),
        source_unknown=fact.provenance.source_unknown,
        author=fact.author,
        created_at=fact.created_at,
        updated_at=fact.updated_at,
    )


@router.get("/notebooks", response_model=list[NotebookOut])
async def list_notebooks(
    request: Request,
    agent_pin: str = Query(min_length=1),
    archived: bool = False,
    principal: Principal = Depends(deps.current_principal),
) -> list[NotebookOut]:
    platform = _platform(request)
    notebooks = await platform.store.list_notebooks(
        tenant_id=principal.tenant_id or principal.id,
        owner=principal.id,
        agent_pin=agent_pin,
        archived=archived,
    )
    return [await _out(platform, nb) for nb in notebooks]


@router.post("/notebooks", response_model=NotebookOut, status_code=201)
async def create_notebook(
    body: NotebookCreate,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> NotebookOut:
    platform = _platform(request)
    async with _errors():
        notebook = await platform.store.create(
            tenant_id=principal.tenant_id or principal.id,
            owner=principal.id,
            agent_pin=body.agent_pin,
            name=body.name.strip(),
            label=body.label,
        )
    return await _out(platform, notebook)


@router.patch("/notebooks/{notebook_id}", response_model=NotebookOut)
async def patch_notebook(
    notebook_id: str,
    body: NotebookPatch,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> NotebookOut:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    async with _errors():
        notebook = await platform.store.set_archived(notebook_id, body.archived)
    return await _out(platform, notebook)


@router.get("/notebooks/{notebook_id}/facts", response_model=list[FactOut])
async def list_facts(
    notebook_id: str,
    request: Request,
    at_revision: int | None = Query(default=None, ge=0),
    principal: Principal = Depends(deps.current_principal),
) -> list[FactOut]:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    view = _view(platform, principal)
    async with _errors():
        facts = (
            await view.facts(notebook_id)
            if at_revision is None
            else await view.facts_at(notebook_id, at_revision)
        )
    return [_fact(f) for f in facts]


@router.post("/notebooks/{notebook_id}/facts", response_model=FactsAdded, status_code=201)
async def add_facts(
    notebook_id: str,
    body: FactsAdd,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> FactsAdded:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    # A person typing a fact in is its source; nothing untracked went into it.
    context = CaptureContext(mode="explicit", actor=principal.id, inherited=())
    async with _errors():
        added = await _view(platform, principal).capture(
            notebook_id, body.facts, author=principal.id, context=context
        )
    return FactsAdded(added=added)


@router.patch("/notebooks/{notebook_id}/facts", status_code=204)
async def edit_facts(
    notebook_id: str,
    body: FactsEdit,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> None:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    edits = [Edit(e.id, e.text) for e in body.edits]
    async with _errors():
        await _view(platform, principal).edit(notebook_id, edits, author=principal.id)


@router.delete("/notebooks/{notebook_id}/facts", status_code=204)
async def delete_facts(
    notebook_id: str,
    request: Request,
    body: FactsDelete = Body(...),
    principal: Principal = Depends(deps.current_principal),
) -> None:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    async with _errors():
        await _view(platform, principal).delete(notebook_id, body.ids, author=principal.id)


@router.post("/notebooks/{notebook_id}/clear", response_model=FactsRemoved)
async def clear_notebook(
    notebook_id: str,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> FactsRemoved:
    """Removes current facts; their history stays (clearing is not erasure)."""
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    async with _errors():
        removed = await _view(platform, principal).clear(notebook_id, author=principal.id)
    return FactsRemoved(removed=removed)


@router.get("/notebooks/{notebook_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(
    notebook_id: str,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> list[RevisionOut]:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    async with _errors():
        revisions = await _view(platform, principal).history(notebook_id)
    return [
        RevisionOut(
            revision=r.revision,
            fact_id=r.fact_id,
            op=r.op,
            text=r.text,
            sensitivity=r.provenance.sensitivity.value,
            at=r.at,
            author=r.author,
        )
        for r in revisions
    ]


@router.post("/notebooks/{notebook_id}/restore", status_code=204)
async def restore_notebook(
    notebook_id: str,
    body: NotebookRestore,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> None:
    platform = _platform(request)
    await _owned(platform, notebook_id, principal)
    async with _errors():
        await _view(platform, principal).restore(notebook_id, body.revision, author=principal.id)


@router.post("/search", response_model=list[MemoryHit])
async def search_memory(
    body: MemorySearch,
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> list[MemoryHit]:
    platform = _platform(request)
    if body.notebook_id is not None:
        notebooks = [(await _owned(platform, body.notebook_id, principal)).id]
    else:
        notebooks = [
            nb.id
            for nb in await platform.store.list_notebooks(
                tenant_id=principal.tenant_id or principal.id,
                owner=principal.id,
                agent_pin=body.agent_pin,
            )
        ]
    hits = await search_across(_view(platform, principal), notebooks, body.query, body.limit)
    return [MemoryHit(notebook_id=nb, fact=fact) for nb, fact in hits]
