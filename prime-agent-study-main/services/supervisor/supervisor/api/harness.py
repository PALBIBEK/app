"""Harness versions and tool implementations, as a user discovers and extends them.

- ``GET /v1/harness_versions``: the versions a tenant can start sessions on
  (shipped ones, and its own registered ones), with status and owner.
- ``GET /v1/harness_versions/{v}``: one version's full manifest.
- ``POST /v1/harness_versions``: register a manifest (the schema of the shipped
  YAML files, as JSON). It belongs to the registering tenant and is immutable;
  the response shows the manifest as stored and any keys that were ignored.
- ``POST /v1/harness_versions/{v}/deprecate``: no new sessions on it.
- ``GET /v1/tools``: every tool a tool set can name, with its parameters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from supervisor.api import deps
from supervisor.db.models import HarnessVersionRow
from supervisor.harness.repository import (
    HarnessVersionConflict,
    deprecate_harness_version,
    harness_owner,
    persist_harness_version,
    sync_harness_version,
    visible_to,
)
from supervisor.harness.services.resolver import HarnessCatalog, HarnessStatus
from supervisor.harness.storage.converters import manifest_to_dict, validate_and_freeze
from supervisor.tools import default_registry

router = APIRouter(tags=["harness"])


def _catalog(request: Request) -> HarnessCatalog:
    return deps.get_manager(request)._catalog


def _summary(manifest: Any, status: HarnessStatus, owner: str | None) -> dict[str, Any]:
    return {
        "harness_version": manifest.harness_version,
        "runtime": manifest.runtime,
        "protocol_version": manifest.protocol_version,
        "requires_sandbox": manifest.requires_sandbox,
        "status": status.value,
        "owner": owner or "shipped",
    }


@router.get("/v1/harness_versions")
async def list_harness_versions(
    request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> list[dict[str, Any]]:
    catalog = _catalog(request)
    factory = deps.get_session_factory(request)
    async with factory() as db:
        owners = dict((await db.execute(select(HarnessVersionRow.harness_version, HarnessVersionRow.owner_id))).all())
    out = []
    for version in await catalog.list_available(include_deprecated=True):
        owner = owners.get(version)
        if visible_to(owner, tenant_id):
            out.append(_summary(await catalog.resolve(version), await catalog.get_status(version), owner))
    return out


async def _visible_manifest(request: Request, tenant_id: str, version: str) -> tuple[Any, str | None]:
    catalog, factory = _catalog(request), deps.get_session_factory(request)
    await sync_harness_version(catalog, factory, version)
    owner = await harness_owner(factory, version)
    if not catalog.has(version) or not visible_to(owner, tenant_id):
        raise HTTPException(status_code=404, detail="unknown harness version")
    return await catalog.resolve(version), owner


@router.get("/v1/harness_versions/{harness_version}")
async def get_harness_version(
    harness_version: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    manifest, owner = await _visible_manifest(request, tenant_id, harness_version)
    status = await _catalog(request).get_status(harness_version)
    return _summary(manifest, status, owner) | {"manifest": manifest_to_dict(manifest)}


@router.post("/v1/harness_versions", status_code=201)
async def register_harness_version(
    body: dict[str, Any], request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> Any:
    try:
        manifest = validate_and_freeze(body)
    except (KeyError, TypeError, ValueError) as exc:  # the validators' errors are ValueErrors
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = manifest_to_dict(manifest)
    try:
        created = await persist_harness_version(
            deps.get_session_factory(request), _catalog(request), manifest, owner=tenant_id
        )
    except HarnessVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    content = _summary(manifest, HarnessStatus.ACTIVE, tenant_id) | {
        "manifest": stored,
        "ignored_keys": sorted(set(body) - set(stored)),
    }
    return JSONResponse(status_code=201 if created else 200, content=content)


@router.post("/v1/harness_versions/{harness_version}/deprecate", status_code=204)
async def deprecate(
    harness_version: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> None:
    _, owner = await _visible_manifest(request, tenant_id, harness_version)
    if owner is None:
        raise HTTPException(status_code=403, detail="shipped harness versions are read-only")
    await deprecate_harness_version(deps.get_session_factory(request), _catalog(request), harness_version)


@router.get("/v1/tools")
async def list_tools(_tenant_id: str = Depends(deps.current_tenant)) -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "tool_class": spec.tool_class.value,
            "parameters": spec.parameters,
        }
        for spec in default_registry().values()
    ]
