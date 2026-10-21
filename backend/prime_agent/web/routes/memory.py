"""Long-term memory routes: a user's notebooks for a harness, and their facts.

Everything goes through the supervisor's notebook API as the signed-in user, so a
notebook of anyone else's answers 404 there. Forgetting facts and clearing a
notebook keep its history: a revision can be restored.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.memory import (
    FactResponse,
    FactsAddedResponse,
    FactsAddRequest,
    FactsEditRequest,
    FactsForgetRequest,
    FactsRemovedResponse,
    MemoryHit,
    MemorySearchRequest,
    NotebookArchiveRequest,
    NotebookCreateRequest,
    NotebookResponse,
    NotebookRestoreRequest,
    RevisionResponse,
)
from prime_agent.services.memory_service import MemoryService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()


def _service() -> MemoryService:
    return MemoryService.from_settings(get_settings())


@router.get("/notebooks", response_model=list[NotebookResponse])
async def list_notebooks(
    db: DbSession,
    current_user: CurrentUser,
    harness_id: str = Query(min_length=1),
    archived: bool = False,
    all_versions: bool = False,
) -> list[dict]:
    """The harness's notebooks: of the version it runs now, or of every version it had.

    Each version keeps its own notebooks (sessions pin one exact version); recall still
    finds facts an older version learned, by meaning.
    """
    service = _service()
    identity = TenantPrincipal.from_user(current_user)
    pins = await service.pins_for(harness_id, current_user.id, db) if all_versions else [
        (await service.pin_for(harness_id, current_user.id, db), None)
    ]
    out: list[dict] = []
    for pin, version in pins:
        out += [nb | {"version": version} for nb in await service.list_notebooks(identity, pin, archived=archived)]
    return out


@router.post("/notebooks", response_model=NotebookResponse, status_code=status.HTTP_201_CREATED)
async def create_notebook(body: NotebookCreateRequest, db: DbSession, current_user: CurrentUser) -> dict:
    service = _service()
    pin = await service.pin_for(body.harness_id, current_user.id, db)
    return await service.create_notebook(TenantPrincipal.from_user(current_user), pin, body.name, body.label)


@router.patch("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def archive_notebook(notebook_id: str, body: NotebookArchiveRequest, current_user: CurrentUser) -> dict:
    return await _service().notebook(
        TenantPrincipal.from_user(current_user), "PATCH", notebook_id, json_body=body.model_dump()
    )


@router.get("/notebooks/{notebook_id}/facts", response_model=list[FactResponse])
async def list_facts(notebook_id: str, current_user: CurrentUser) -> list[dict]:
    return await _service().notebook(TenantPrincipal.from_user(current_user), "GET", notebook_id, "facts")


@router.post("/notebooks/{notebook_id}/facts", response_model=FactsAddedResponse, status_code=201)
async def add_facts(notebook_id: str, body: FactsAddRequest, current_user: CurrentUser) -> dict:
    return await _service().notebook(
        TenantPrincipal.from_user(current_user), "POST", notebook_id, "facts",
        json_body=body.model_dump(), expected=(201,),
    )


@router.patch("/notebooks/{notebook_id}/facts", status_code=204, response_class=Response)
async def edit_facts(notebook_id: str, body: FactsEditRequest, current_user: CurrentUser) -> Response:
    await _service().notebook(
        TenantPrincipal.from_user(current_user), "PATCH", notebook_id, "facts",
        json_body=body.model_dump(), expected=(204,),
    )
    return Response(status_code=204)


@router.post("/notebooks/{notebook_id}/forget", status_code=204, response_class=Response)
async def forget_facts(notebook_id: str, body: FactsForgetRequest, current_user: CurrentUser) -> Response:
    """Forget facts (``DELETE …/facts`` upstream; a POST here so the body is never dropped)."""
    await _service().notebook(
        TenantPrincipal.from_user(current_user), "DELETE", notebook_id, "facts",
        json_body=body.model_dump(), expected=(204,),
    )
    return Response(status_code=204)


@router.post("/notebooks/{notebook_id}/clear", response_model=FactsRemovedResponse)
async def clear_notebook(notebook_id: str, current_user: CurrentUser) -> dict:
    return await _service().notebook(TenantPrincipal.from_user(current_user), "POST", notebook_id, "clear")


@router.get("/notebooks/{notebook_id}/revisions", response_model=list[RevisionResponse])
async def list_revisions(notebook_id: str, current_user: CurrentUser) -> list[dict]:
    return await _service().notebook(TenantPrincipal.from_user(current_user), "GET", notebook_id, "revisions")


@router.post("/notebooks/{notebook_id}/restore", status_code=204, response_class=Response)
async def restore_notebook(notebook_id: str, body: NotebookRestoreRequest, current_user: CurrentUser) -> Response:
    await _service().notebook(
        TenantPrincipal.from_user(current_user), "POST", notebook_id, "restore",
        json_body=body.model_dump(), expected=(204,),
    )
    return Response(status_code=204)


@router.post("/search", response_model=list[MemoryHit])
async def search_memory(body: MemorySearchRequest, db: DbSession, current_user: CurrentUser) -> list[dict]:
    service = _service()
    pin = await service.pin_for(body.harness_id, current_user.id, db)
    return await service.search(TenantPrincipal.from_user(current_user), pin, body.query, body.notebook_id, body.limit)
