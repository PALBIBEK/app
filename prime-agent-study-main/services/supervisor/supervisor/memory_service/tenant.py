"""Tenant isolation gate — fail loud if tenant omitted (app-level; RLS for Postgres)."""

from __future__ import annotations

from supervisor.memory_service.types import MemoryError, Scope


class TenantRequired(MemoryError):
    code = "TENANT_REQUIRED"


class TenantMismatch(MemoryError):
    code = "TENANT_MISMATCH"


def require_tenant(tenant: str | None) -> str:
    if not tenant or not str(tenant).strip():
        raise TenantRequired("TENANT_REQUIRED: tenant must be non-empty")
    return str(tenant).strip()


def require_scope(scope: Scope) -> Scope:
    require_tenant(scope.tenant)
    if not scope.subject or not scope.namespace:
        raise TenantRequired("SCOPE_REQUIRED: subject and namespace required with tenant")
    return scope


