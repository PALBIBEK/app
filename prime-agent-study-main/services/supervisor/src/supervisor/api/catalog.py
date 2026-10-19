"""Composition catalog HTTP — publish / deprecate persisted artifacts."""

from __future__ import annotations

from typing import Any, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from supervisor.api import deps
from supervisor.api.schemas import CatalogArtifactOut
from supervisor.catalog import CatalogError, ArtifactKind, ToolSet
from supervisor.catalog.services import require_implemented
from supervisor.catalog.services.publish_checks import check_publishable
from supervisor.catalog.storage import CompositionCatalog, artifact_from_dict, artifact_to_dict

from supervisor.catalog.core.errors import CatalogOwnershipRequired, NotOffered
from supervisor.catalog.repository import (
    artifact_summary,
    persist_deprecate,
    persist_offer,
    persisted_owner,
)
from supervisor.catalog.storage.composition import SYSTEM_OWNER

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])

KindPath = Literal["prompt_pack", "tool_set", "composition", "sandbox_profile", "memory_profile"]


_CATALOG_ERROR_STATUS: Final[dict[str, int]] = {
    "NOT_OFFERED": 404,
    "DIGEST_MISSING": 404,
    "DIGEST_MISMATCH": 422,
    "INVALID_DIGEST": 422,
    "INVALID_ARTIFACT": 422,
    "FLOATING_REF": 422,
    "COMPOSITION_INCOMPLETE": 422,
    "CATALOG_PERMISSION_DENIED": 403,
    "CATALOG_OWNERSHIP_REQUIRED": 403,
}


def _catalog_error(exc: CatalogError) -> HTTPException:
    return HTTPException(
        status_code=_CATALOG_ERROR_STATUS.get(exc.error_code, 409),
        detail={"message": str(exc), "code": exc.error_code},
    )


@router.post("/{kind}/publish", response_model=CatalogArtifactOut)
async def publish_artifact(
    kind: KindPath,
    body: dict[str, Any],
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> CatalogArtifactOut:
    """Publish a version of ``body``; its name belongs to the first tenant that publishes it."""
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    factory = deps.get_session_factory(request)
    try:
        artifact = artifact_from_dict(kind, body)
        check_publishable(kind, body, artifact)  # 422: unknown fields, unservable models, ...
        if isinstance(artifact, ToolSet):
            require_implemented(artifact)  # 422 before it can be pinned
        if (owner := await persisted_owner(factory, kind, artifact.name)) is not None:
            catalog.claim(kind, artifact.name, owner)  # another replica may have published it
        offered = catalog.offer(artifact, owner=tenant_id)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await persist_offer(
        factory, catalog, kind, offered.name, offered.digest, created_by=tenant_id
    )
    summary = artifact_summary(offered)
    return CatalogArtifactOut(kind=kind, **summary)


@router.post("/{kind}/{artifact_id}/{digest}/deprecate", status_code=204)
async def deprecate_artifact(
    kind: KindPath,
    artifact_id: str,
    digest: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    """Only the owning tenant deprecates; shipped artifacts are read-only to tenants."""
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    factory = deps.get_session_factory(request)
    owner = await persisted_owner(factory, kind, artifact_id) or catalog.owner(kind, artifact_id)
    try:
        if owner not in (None, tenant_id):
            if owner == SYSTEM_OWNER:
                raise CatalogOwnershipRequired(
                    principal_id=tenant_id, artifact_kind=kind, artifact_id=artifact_id, owner_id=owner
                )
            raise NotOffered(kind=kind, artifact_id=artifact_id, digest=digest)  # not disclosed
        catalog.deprecate(kind, artifact_id, digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    await persist_deprecate(factory, kind, artifact_id, digest, deprecated_by=tenant_id)


@router.get("/compositions", response_model=list[CatalogArtifactOut])
async def list_compositions(
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    status: str | None = None,
) -> list[CatalogArtifactOut]:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    arts = [
        a
        for a in catalog.list_artifacts("composition", status=status)
        if catalog.visible_to("composition", a.name, tenant_id)
    ]
    return [
        CatalogArtifactOut(kind="composition", **artifact_summary(a)) for a in arts
    ]


@router.get("/{kind}/{artifact_id}/{digest}", response_model=CatalogArtifactOut)
async def get_artifact(
    kind: KindPath,
    artifact_id: str,
    digest: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> CatalogArtifactOut:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    try:
        if not catalog.visible_to(kind, artifact_id, tenant_id):
            raise NotOffered(kind=kind, artifact_id=artifact_id, digest=digest)
        art = catalog.get(kind, artifact_id, digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return CatalogArtifactOut(kind=kind, **artifact_summary(art), body=artifact_to_dict(art))
