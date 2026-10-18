"""Composition catalog HTTP — publish / deprecate persisted artifacts."""

from __future__ import annotations

from typing import Any, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from supervisor.api import deps
from supervisor.api.schemas import CatalogArtifactOut
from supervisor.catalog import CatalogError, ArtifactKind
from supervisor.catalog.storage import CompositionCatalog, artifact_from_dict

from supervisor.catalog.repository import artifact_summary, persist_deprecate, persist_offer

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
    _tenant_id: str = Depends(deps.current_tenant),
) -> CatalogArtifactOut:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    try:
        artifact = artifact_from_dict(kind, body)
        offered = catalog.offer(artifact)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    factory = deps.get_session_factory(request)
    await persist_offer(factory, catalog, kind, offered.name, offered.digest)
    summary = artifact_summary(offered)
    return CatalogArtifactOut(kind=kind, **summary)


@router.post("/{kind}/{artifact_id}/{digest}/deprecate", status_code=204)
async def deprecate_artifact(
    kind: KindPath,
    artifact_id: str,
    digest: str,
    request: Request,
    _tenant_id: str = Depends(deps.current_tenant),
) -> None:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    try:
        catalog.deprecate(kind, artifact_id, digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    factory = deps.get_session_factory(request)
    await persist_deprecate(factory, kind, artifact_id, digest)


@router.get("/compositions", response_model=list[CatalogArtifactOut])
async def list_compositions(
    request: Request,
    _tenant_id: str = Depends(deps.current_tenant),
    status: str | None = None,
) -> list[CatalogArtifactOut]:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    arts = catalog.list_artifacts("composition", status=status)
    return [
        CatalogArtifactOut(kind="composition", **artifact_summary(a)) for a in arts
    ]


@router.get("/{kind}/{artifact_id}/{digest}", response_model=CatalogArtifactOut)
async def get_artifact(
    kind: KindPath,
    artifact_id: str,
    digest: str,
    request: Request,
    _tenant_id: str = Depends(deps.current_tenant),
) -> CatalogArtifactOut:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    try:
        art = catalog.get(kind, artifact_id, digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return CatalogArtifactOut(kind=kind, **artifact_summary(art))
