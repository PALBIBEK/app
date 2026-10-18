"""Scope membership admin API — durable grants (D11).

Manage principal membership and individual grants. Requires ``scope:admin``.
Distinct from outer product Team roles (Settings harness RBAC).

Also exposes ``POST /{scope_id}/ensure`` for upload-before-session owner bootstrap (D9).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.db.models import ScopeRow
from supervisor.seams.services import (
    ForbiddenError,
    Grant,
    GrantDenied,
    GrantStore,
    NotScopeMember,
    Principal,
    UnauthorizedError,
)

router = APIRouter(prefix="/v1/scopes", tags=["scopes"])

_DEFAULT_INVITE_GRANTS: tuple[Grant, ...] = (
    Grant.WORKSPACE_READ,
    Grant.SANDBOX_EXEC,
)


class ScopeMemberOut(BaseModel):
    principal_id: str
    grants: list[str]


class InviteMemberIn(BaseModel):
    principal_id: str = Field(min_length=1)
    grants: list[str] | None = None


class GrantMutationIn(BaseModel):
    principal_id: str = Field(min_length=1)
    grant: str
    action: Literal["grant", "revoke"]


class EnsureScopeOut(BaseModel):
    scope_id: str
    status: str = "ok"


def _grant_store(request: Request) -> GrantStore:
    return deps.get_grant_store(request)


async def _require_scope_admin(
    request: Request,
    scope_id: str,
    *,
    authorization: str | None,
    x_tenant_id: str | None,
    x_principal_id: str | None,
) -> Principal:
    """``scope:admin`` gate — denial returns 403 (admin surface, not enumeration)."""
    try:
        principal = await deps.current_principal(
            request,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_principal_id=x_principal_id,
        )
        authz = deps.get_authz(request)
        return await authz.require(principal, scope_id, Grant.SCOPE_ADMIN)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (GrantDenied, NotScopeMember, ForbiddenError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _parse_grants(raw: list[str] | None) -> list[Grant]:
    if not raw:
        return list(_DEFAULT_INVITE_GRANTS)
    out: list[Grant] = []
    for item in raw:
        try:
            out.append(Grant(item))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"unknown grant: {item!r}"
            ) from exc
    return out


async def _member_rows(
    store: GrantStore, scope_id: str
) -> list[ScopeMemberOut]:
    list_ids = getattr(store, "list_member_ids", None)
    if list_ids is None:
        raise HTTPException(status_code=503, detail="grant store missing list_member_ids")
    member_ids = await list_ids(scope_id)
    rows: list[ScopeMemberOut] = []
    for pid in member_ids:
        grants = await store.list_grants(pid, scope_id)
        rows.append(
            ScopeMemberOut(
                principal_id=pid,
                grants=sorted(str(g) for g in grants),
            )
        )
    return rows


@router.post("/{scope_id}/ensure", response_model=EnsureScopeOut)
async def ensure_workspace_scope(
    scope_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> EnsureScopeOut:
    """Idempotently ensure durable scope + owner grants (BFF upload-before-session).

    New scopes: create ``ScopeRow`` and ``bootstrap_owner`` for the caller.
    Existing scopes: members get 200; non-members never re-bootstrap (WP-M7 IDOR).
    Auth fail-closed via ``current_principal`` (401 when required).
    """
    principal = await deps.current_principal(
        request,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    if not scope_id.strip():
        raise HTTPException(status_code=400, detail="scope_id required")

    factory = deps.get_session_factory(request)
    store = _grant_store(request)
    created = False
    async with factory() as db:
        scope = await db.get(ScopeRow, scope_id)
        if scope is None:
            db.add(
                ScopeRow(
                    id=scope_id,
                    type="personal",
                    owner_principal_id=principal.id,
                    name=scope_id,
                )
            )
            await db.commit()
            created = True

    if created:
        await store.bootstrap_owner(principal.id, scope_id)
    elif not await store.is_member(principal.id, scope_id):
        # Existing scope — no stranger re-bootstrap (same rule as session attach).
        raise HTTPException(status_code=403, detail="forbidden")

    return EnsureScopeOut(scope_id=scope_id, status="ok")


@router.get("/{scope_id}/members", response_model=list[ScopeMemberOut])
async def list_scope_members(
    scope_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> list[ScopeMemberOut]:
    await _require_scope_admin(
        request,
        scope_id,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    return await _member_rows(_grant_store(request), scope_id)


@router.post("/{scope_id}/members", response_model=ScopeMemberOut, status_code=201)
async def invite_scope_member(
    scope_id: str,
    body: InviteMemberIn,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> ScopeMemberOut:
    await _require_scope_admin(
        request,
        scope_id,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _grant_store(request)
    grants = _parse_grants(body.grants)
    await store.add_member(body.principal_id, scope_id)
    for g in grants:
        await store.grant(body.principal_id, scope_id, g)
    member_grants = await store.list_grants(body.principal_id, scope_id)
    return ScopeMemberOut(
        principal_id=body.principal_id,
        grants=sorted(str(g) for g in member_grants),
    )


@router.delete("/{scope_id}/members/{principal_id}", status_code=204)
async def revoke_scope_member(
    scope_id: str,
    principal_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> Response:
    await _require_scope_admin(
        request,
        scope_id,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    await _grant_store(request).remove_member(principal_id, scope_id)
    return Response(status_code=204)


@router.post("/{scope_id}/grants", response_model=ScopeMemberOut)
async def mutate_scope_grant(
    scope_id: str,
    body: GrantMutationIn,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> ScopeMemberOut:
    await _require_scope_admin(
        request,
        scope_id,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    try:
        grant = Grant(body.grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown grant: {body.grant!r}") from exc
    store = _grant_store(request)
    if body.action == "grant":
        await store.grant(body.principal_id, scope_id, grant)
    else:
        await store.revoke(body.principal_id, scope_id, grant)
    member_grants = await store.list_grants(body.principal_id, scope_id)
    return ScopeMemberOut(
        principal_id=body.principal_id,
        grants=sorted(str(g) for g in member_grants),
    )
