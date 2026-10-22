"""Environments: named sandbox setups (where commands run, what they may touch, when the
sandbox starts and stops) that several harnesses share."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from prime_agent.schemas.environment import EnvironmentView, EnvironmentWrite
from prime_agent.services.environment_service import EnvironmentService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()


@router.get("", response_model=list[EnvironmentView])
async def list_environments(db: DbSession, current_user: CurrentUser) -> list[EnvironmentView]:
    """Your environments, with the harnesses that use each."""
    return await EnvironmentService.list(current_user.id, db)


@router.post("", response_model=EnvironmentView, status_code=status.HTTP_201_CREATED)
async def create_environment(body: EnvironmentWrite, db: DbSession, current_user: CurrentUser) -> EnvironmentView:
    return await EnvironmentService.create(current_user.id, body, db)


@router.get("/{environment_id}", response_model=EnvironmentView)
async def get_environment(environment_id: str, db: DbSession, current_user: CurrentUser) -> EnvironmentView:
    return await EnvironmentService.view(await EnvironmentService.get_owned(environment_id, current_user.id, db), db)


@router.put("/{environment_id}", response_model=EnvironmentView)
async def update_environment(
    environment_id: str, body: EnvironmentWrite, db: DbSession, current_user: CurrentUser
) -> EnvironmentView:
    """Change it: every harness using it shows an unpublished change until republished."""
    env = await EnvironmentService.get_owned(environment_id, current_user.id, db)
    return await EnvironmentService.update(env, body, db)


@router.delete("/{environment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_environment(environment_id: str, db: DbSession, current_user: CurrentUser) -> Response:
    """Refused (409) while a harness uses it."""
    env = await EnvironmentService.get_owned(environment_id, current_user.id, db)
    await EnvironmentService.delete(env, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
