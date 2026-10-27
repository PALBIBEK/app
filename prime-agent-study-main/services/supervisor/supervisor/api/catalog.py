"""The composition catalog over HTTP: publish, check, deprecate, read, list, and usage.

A name belongs to the first tenant that publishes it; shipped artifacts are
read-only to every tenant. Another tenant's artifact is answered as absent (404),
so names cannot be probed. Errors are ``CatalogError.to_dict()``: ``code``,
``message``, ``retryable`` and ``detail``.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.api.schemas import CatalogArtifactOut
from supervisor.catalog import (
    AgentComposition,
    Artifact,
    ArtifactKind,
    CatalogError,
    CatalogOwnershipRequired,
    NotOffered,
    ToolSet,
    content_digest,
    validate_digest_format,
)
from supervisor.catalog.core.digests import is_unset_digest
from supervisor.catalog.repository import (
    artifact_summary,
    persist_deprecate,
    persist_offer,
    persisted_owner,
)
from supervisor.catalog.services import (
    check_publishable,
    require_implemented,
    require_sticky_compatible,
)
from supervisor.catalog.storage import (
    SYSTEM_OWNER,
    CompositionCatalog,
    artifact_from_dict,
    artifact_to_dict,
)
from supervisor.catalog.usage import artifact_usage
from supervisor.harness.core.enums import RuntimeMode

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
    "COMPOSITION_INCOMPATIBLE": 422,
    "COMPOSITION_DIGEST_REQUIRED": 422,
    "HARNESS_VERSION_MISMATCH": 422,
    "CATALOG_PERMISSION_DENIED": 403,
    "CATALOG_OWNERSHIP_REQUIRED": 403,
}


def _catalog_error(exc: CatalogError) -> HTTPException:
    return HTTPException(status_code=_CATALOG_ERROR_STATUS.get(exc.error_code, 409), detail=exc.to_dict())


def _out(kind: str, artifact: Artifact, *, with_body: bool = False) -> CatalogArtifactOut:
    """An artifact as the API shows it: its summary and metadata (and, on a single read, its body)."""
    out = {"kind": kind, **artifact_summary(artifact)}
    if with_body:
        out["body"] = artifact_to_dict(artifact)
    return CatalogArtifactOut.model_validate(out)


def _digest(digest: str) -> str:
    """A path's digest, or 422 ``INVALID_DIGEST`` (before any lookup, so no 404 for a typo)."""
    try:
        validate_digest_format(digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return digest


async def _harness_is_sticky(request: Request, harness_version: str) -> bool | None:
    """Whether ``harness_version`` keeps its sandbox between turns (``None`` if unknown here)."""
    try:
        manifest = await deps.get_harness_catalog(request).resolve(harness_version)
    except Exception:  # noqa: BLE001 - an unknown harness is reported when a session is created
        return None
    return manifest.mode == RuntimeMode.STICKY


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
        if isinstance(artifact, AgentComposition):
            sticky = await _harness_is_sticky(request, artifact.harness_version)
            require_sticky_compatible(catalog, artifact, sticky=bool(sticky))
        if (owner := await persisted_owner(factory, kind, artifact.name)) is not None:
            catalog.claim(kind, artifact.name, owner)  # another replica may have published it
        offered = catalog.offer(artifact, owner=tenant_id)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stored = await persist_offer(
        factory,
        catalog,
        kind,
        offered.name,
        offered.digest,
        owner_id=tenant_id,
        created_by=deps.request_principal_id(request, tenant_id),
    )
    return _out(kind, stored)


class CheckOut(BaseModel):
    check: str
    ok: bool
    message: str = ""
    code: str | None = None


class PublishCheckOut(BaseModel):
    ok: bool
    digest: str | None = None
    checks: list[CheckOut]


@router.post("/{kind}/check", response_model=PublishCheckOut)
async def check_artifact(
    kind: KindPath,
    body: dict[str, Any],
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> PublishCheckOut:
    """What publishing ``body`` would find, without publishing it (nothing is stored or claimed).

    Every check runs, so a draft shows all its problems at once; the digest is
    the one it would be published under.
    """
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    factory = deps.get_session_factory(request)
    checks: list[CheckOut] = []

    def run(check: str, step: Any) -> None:
        try:
            step()
            checks.append(CheckOut(check=check, ok=True))
        except CatalogError as exc:
            checks.append(CheckOut(check=check, ok=False, message=str(exc), code=exc.error_code))

    parsed = "It reads as a valid " + kind.replace("_", " ")
    try:
        artifact = artifact_from_dict(kind, body)
    except (CatalogError, KeyError, TypeError, ValueError) as exc:
        code = getattr(exc, "error_code", None) or type(exc).__name__
        return PublishCheckOut(ok=False, checks=[CheckOut(check=parsed, ok=False, message=str(exc), code=code)])
    checks.append(CheckOut(check=parsed, ok=True))

    run("Every field is known and every setting can work", lambda: check_publishable(kind, body, artifact))
    if isinstance(artifact, ToolSet):
        run("Every tool has an implementation", lambda: require_implemented(artifact))
    if isinstance(artifact, AgentComposition):
        sticky = await _harness_is_sticky(request, artifact.harness_version)
        run(
            "Its sandboxes can be kept as its harness keeps them",
            lambda: require_sticky_compatible(catalog, artifact, sticky=bool(sticky)),
        )

    # Ownership without claim(): claiming would record the caller as the owner.
    owner = await persisted_owner(factory, kind, artifact.name) or catalog.owner(kind, artifact.name)
    if owner in (None, tenant_id):
        checks.append(CheckOut(check="The name is yours to publish under", ok=True))
    else:
        taken = CatalogOwnershipRequired(
            principal_id=tenant_id, artifact_kind=kind, artifact_id=artifact.name, owner_id=owner
        )
        checks.append(
            CheckOut(check="The name is yours to publish under", ok=False, message=str(taken), code=taken.error_code)
        )

    expected = content_digest(artifact.body_for_digest())
    if is_unset_digest(artifact.digest) or artifact.digest == expected:
        checks.append(CheckOut(check="Its digest matches its content", ok=True))
    else:
        checks.append(
            CheckOut(
                check="Its digest matches its content",
                ok=False,
                message=f"the body hashes to {expected}, not {artifact.digest}",
                code="DIGEST_MISMATCH",
            )
        )
    return PublishCheckOut(ok=all(c.ok for c in checks), digest=expected, checks=checks)


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
    _digest(digest)
    owner = await persisted_owner(factory, kind, artifact_id) or catalog.owner(kind, artifact_id)
    try:
        if owner not in (None, tenant_id):
            if owner == SYSTEM_OWNER:
                raise CatalogOwnershipRequired(
                    principal_id=tenant_id, artifact_kind=kind, artifact_id=artifact_id, owner_id=owner
                )
            raise NotOffered(kind=kind, artifact_id=artifact_id, digest=digest)  # not disclosed
        catalog.get(kind, artifact_id, digest)  # 404 before anything is written
        await persist_deprecate(
            factory,
            catalog,
            kind,
            artifact_id,
            digest,
            deprecated_by=deps.request_principal_id(request, tenant_id),
        )
    except CatalogError as exc:
        raise _catalog_error(exc) from exc


@router.get("/compositions", response_model=list[CatalogArtifactOut])
async def list_compositions(
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    status: str | None = None,
) -> list[CatalogArtifactOut]:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    return [
        _out("composition", a)
        for a in catalog.artifacts(ArtifactKind.COMPOSITION, status=status)
        if catalog.visible_to(ArtifactKind.COMPOSITION, a.name, tenant_id)
    ]


class VersionUsageOut(BaseModel):
    digest: str
    sessions: int


class ArtifactUsageOut(BaseModel):
    artifact_kind: str
    artifact_id: str
    artifact_digest: str | None
    session_count: int
    active_sessions: int
    unique_principals: int
    first_used_at: str | None
    last_used_at: str | None
    versions: list[VersionUsageOut]


@router.get("/{kind}/{artifact_id}/usage", response_model=ArtifactUsageOut)
async def get_usage(
    kind: KindPath,
    artifact_id: str,
    request: Request,
    digest: str | None = Query(default=None, description="One version; else every version"),
    tenant_id: str = Depends(deps.current_tenant),
) -> ArtifactUsageOut:
    """How many of the caller's sessions pin ``artifact_id``, and which versions they pin."""
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    if digest is not None:
        _digest(digest)
    if not catalog.visible_to(kind, artifact_id, tenant_id) or not catalog.list_versions(kind, artifact_id):
        raise _catalog_error(NotOffered(kind=kind, artifact_id=artifact_id, digest=digest or ""))
    stats = await artifact_usage(
        deps.get_session_factory(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        kind=ArtifactKind(kind),
        artifact_id=artifact_id,
        digest=digest,
    )
    return ArtifactUsageOut(**stats.to_dict())


@router.get("/{kind}/{artifact_id}/{digest}", response_model=CatalogArtifactOut)
async def get_artifact(
    kind: KindPath,
    artifact_id: str,
    digest: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> CatalogArtifactOut:
    catalog: CompositionCatalog = deps.get_composition_catalog(request)
    _digest(digest)
    try:
        if not catalog.visible_to(kind, artifact_id, tenant_id):
            raise NotOffered(kind=kind, artifact_id=artifact_id, digest=digest)
        art = catalog.get(kind, artifact_id, digest)
    except CatalogError as exc:
        raise _catalog_error(exc) from exc
    return _out(kind, art, with_body=True)
