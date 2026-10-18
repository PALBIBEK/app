"""Authenticated, owner-scoped tool CRUD routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from prime_agent.schemas.tool import (
    ToolCreate,
    ToolListResponse,
    ToolResponse,
    ToolUpdate,
)
from prime_agent.services.tool_service import ToolService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=ToolListResponse)
async def list_tools(
    db: DbSession,
    current_user: CurrentUser,
    page: Page = 1,
    page_size: PageSize = 20,
    category: str | None = None,
) -> ToolListResponse:
    """List owned tools, optionally filtered by category."""
    if category is not None and category not in ToolService.VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail="Invalid tool category",
        )

    filters = {"user_id": current_user.id}
    if category is not None:
        filters["category"] = category

    tools = await ToolService.aget_all(
        db,
        skip=(page - 1) * page_size,
        limit=page_size,
        **filters,
    )
    total = await ToolService.count(db, **filters)
    return ToolListResponse(
        data=tools,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    tool_data: ToolCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> ToolResponse:
    """Create a tool owned by the authenticated user."""
    data = tool_data.model_dump()
    data.update(user_id=current_user.id, created_by=current_user.id)
    return await ToolService.acreate(data, db)


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tool_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> ToolResponse:
    """Return an owned tool without exposing other users' resources."""
    return await ToolService.get_owned(tool_id, current_user.id, db)


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: str,
    tool_data: ToolUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> ToolResponse:
    """Update an owned tool."""
    tool = await ToolService.get_owned(tool_id, current_user.id, db)
    data = tool_data.model_dump(exclude_unset=True)
    if data.get("name") == tool.name:
        data.pop("name")
    data.update(user_id=current_user.id, updated_by=current_user.id)
    return await ToolService.aupdate(tool_id, data, db)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_tool(
    tool_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Delete an owned tool."""
    await ToolService.get_owned(tool_id, current_user.id, db)
    await ToolService.adelete(tool_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
