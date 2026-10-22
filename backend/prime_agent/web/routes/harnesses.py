"""Authenticated, owner-scoped harness CRUD + D3 bind routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.harness import (
    HarnessBindRequest,
    HarnessCreate,
    HarnessListResponse,
    HarnessResponse,
    HarnessUpdate,
)
from prime_agent.services.harness_bind_service import (
    BindDomainError,
    HarnessBindService,
    raise_bind_http,
)
from prime_agent.services.environment_service import EnvironmentService
from prime_agent.schemas.harness_manifest import (
    manifest_body,
    HarnessManifest,
    HarnessManifestView,
    HarnessPublishResponse,
    HarnessReleaseView,
)
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_publish_service import (
    HarnessPublisher,
    PublishError,
    load_draft,
    releases,
    save_draft,
)
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=HarnessListResponse)
async def list_harnesses(
    db: DbSession,
    current_user: CurrentUser,
    page: Page = 1,
    page_size: PageSize = 20,
) -> HarnessListResponse:
    """List harnesses owned by the authenticated user."""
    skip = (page - 1) * page_size
    harnesses = await HarnessService.aget_all(
        db,
        skip=skip,
        limit=page_size,
        user_id=current_user.id,
    )
    total = await HarnessService.count(db, user_id=current_user.id)
    return HarnessListResponse(
        data=[HarnessResponse.from_harness(h) for h in harnesses],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=HarnessResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_harness(
    harness_data: HarnessCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> HarnessResponse:
    """Create a harness owned by the authenticated user."""
    data = harness_data.model_dump()
    data.update(user_id=current_user.id, created_by=current_user.id)
    harness = await HarnessService.acreate(data, db)
    return HarnessResponse.from_harness(harness)


@router.get("/{harness_id}", response_model=HarnessResponse)
async def get_harness(
    harness_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> HarnessResponse:
    """Return an owned harness without exposing other users' resources."""
    harness = await HarnessService.get_owned(harness_id, current_user.id, db)
    return HarnessResponse.from_harness(harness)


@router.put("/{harness_id}", response_model=HarnessResponse)
async def update_harness(
    harness_id: str,
    harness_data: HarnessUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> HarnessResponse:
    """Update an owned harness."""
    harness = await HarnessService.get_owned(harness_id, current_user.id, db)
    data = harness_data.model_dump(exclude_unset=True)
    if data.get("name") == harness.name:
        data.pop("name")
    data.update(user_id=current_user.id, updated_by=current_user.id)
    updated = await HarnessService.aupdate(harness_id, data, db)
    return HarnessResponse.from_harness(updated)


@router.delete(
    "/{harness_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_harness(
    harness_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Delete an owned harness."""
    await HarnessService.get_owned(harness_id, current_user.id, db)
    await HarnessService.adelete(harness_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post(
    "/{harness_id}/bind",
    response_model=HarnessResponse,
)
async def bind_harness(
    harness_id: str,
    db: DbSession,
    current_user: CurrentUser,
    body: HarnessBindRequest | None = None,
) -> HarnessResponse:
    """Pin an owned harness to an offered composition (D3 MVP)."""
    request = body if body is not None else HarnessBindRequest()
    identity = TenantPrincipal.from_user(current_user)
    service = HarnessBindService.from_settings(get_settings())
    try:
        return await service.bind_to_composition(
            harness_id=harness_id,
            user_id=current_user.id,
            identity=identity,
            request=request,
            db=db,
        )
    except BindDomainError as exc:
        raise_bind_http(exc)


# --- the manifest builder: draft, publish, releases ------------------------------


def _publisher() -> HarnessPublisher:
    return HarnessPublisher(AiClient.from_settings(get_settings()))


async def _manifest_view(harness_id: str, user_id: str, db) -> HarnessManifestView:
    """The draft as it would publish: a named environment's settings are its sandbox."""
    draft = await EnvironmentService.resolve(await load_draft(harness_id, db), user_id, db)
    history = await releases(harness_id, db)
    latest = history[0] if history else None
    return HarnessManifestView(
        manifest=draft,
        published_version=latest.version if latest else None,
        unpublished_changes=latest is None or latest.manifest != manifest_body(draft),
    )


@router.get("/{harness_id}/manifest", response_model=HarnessManifestView)
async def get_manifest(harness_id: str, db: DbSession, current_user: CurrentUser) -> HarnessManifestView:
    """The draft (a new harness starts as the shipped coding agent) and whether it is published."""
    await HarnessService.get_owned(harness_id, current_user.id, db)
    return await _manifest_view(harness_id, current_user.id, db)


@router.put("/{harness_id}/manifest", response_model=HarnessManifestView)
async def put_manifest(
    harness_id: str, manifest: HarnessManifest, db: DbSession, current_user: CurrentUser
) -> HarnessManifestView:
    """Save the draft. Validation errors name the field (``loc``), so the builder shows them in place."""
    await HarnessService.get_owned(harness_id, current_user.id, db)
    manifest = await EnvironmentService.resolve(manifest, current_user.id, db)  # 422 if it names none of yours
    await save_draft(harness_id, manifest, db)
    return await _manifest_view(harness_id, current_user.id, db)


@router.post("/{harness_id}/publish", response_model=HarnessPublishResponse)
async def publish_harness(harness_id: str, db: DbSession, current_user: CurrentUser) -> HarnessPublishResponse:
    """Publish the draft as the next release; an unchanged draft publishes nothing."""
    harness = await HarnessService.get_owned(harness_id, current_user.id, db)
    try:
        published = await _publisher().publish(
            harness, user_id=current_user.id, identity=TenantPrincipal.from_user(current_user), db=db
        )
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return HarnessPublishResponse(
        release=HarnessReleaseView.model_validate(published.release, from_attributes=True),
        created=published.created,
    )


@router.get("/{harness_id}/releases", response_model=list[HarnessReleaseView])
async def list_releases(harness_id: str, db: DbSession, current_user: CurrentUser) -> list[HarnessReleaseView]:
    await HarnessService.get_owned(harness_id, current_user.id, db)
    return [HarnessReleaseView.model_validate(r, from_attributes=True) for r in await releases(harness_id, db)]
