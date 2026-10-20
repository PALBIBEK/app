"""Tenant secrets: write-only credentials the supervisor uses on the tenant's behalf.

A value goes in and never comes out: listing returns names and expiry only.
``mcp:<origin>`` (``mcp:https://tools.example.com``) authenticates the MCP
server at that origin for every session of the tenant (``supervisor.vault.credentials``).

Who in a tenant may change its secrets is the caller's decision (the product's
settings RBAC); this API takes the tenant from the verified service identity.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.vault import Owner, Vault

router = APIRouter(prefix="/v1/secrets", tags=["secrets"])

_NAME = r"^[A-Za-z0-9][A-Za-z0-9:._/\-]{0,127}$"
_MAX_VALUE = 16 * 1024


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=_MAX_VALUE)
    expires_at: datetime | None = None


class SecretOut(BaseModel):
    name: str


class SecretList(BaseModel):
    names: list[str]


def _vault(request: Request) -> Vault:
    vault = getattr(deps._state(request), "vault", None)
    if vault is None:
        raise HTTPException(status_code=503, detail="vault not configured")
    return vault


def _forget(request: Request, tenant_id: str, name: str) -> None:
    credentials = getattr(deps.get_manager(request), "_credentials", None)
    if credentials is not None:
        credentials.forget(tenant_id, name)


@router.get("", response_model=SecretList)
async def list_secrets(request: Request, tenant_id: str = Depends(deps.current_tenant)) -> SecretList:
    return SecretList(names=await _vault(request).names(Owner.tenant(tenant_id)))


@router.put("/{name:path}", response_model=SecretOut)
async def put_secret(
    request: Request,
    body: SecretIn,
    name: str,
    tenant_id: str = Depends(deps.current_tenant),
) -> SecretOut:
    _check_name(name)
    await _vault(request).put(Owner.tenant(tenant_id), name, body.value, expires_at=body.expires_at)
    _forget(request, tenant_id, name)
    return SecretOut(name=name)


@router.delete("/{name:path}", status_code=204)
async def delete_secret(
    request: Request, name: str, tenant_id: str = Depends(deps.current_tenant)
) -> Response:
    _check_name(name)
    if not await _vault(request).delete(Owner.tenant(tenant_id), name):
        raise HTTPException(status_code=404, detail="no such secret")
    _forget(request, tenant_id, name)
    return Response(status_code=204)


def _check_name(name: str) -> None:
    if not re.fullmatch(_NAME, name):
        raise HTTPException(status_code=422, detail="invalid secret name")
