"""Who may see and drive a session.

A session belongs to the principal that created it. Two kinds of access are
tenant-wide on purpose:

- a session created by a tenant-level caller (no user: its owner is the
  tenant itself, or was never recorded) is shared by the whole tenant;
- a tenant-level caller (a service acting for the tenant, whose principal is
  the tenant id) sees every session of its tenant.

Everyone else sees only their own sessions; anything else is reported as not
found, so a session's existence is not disclosed either.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from supervisor.db.models import SessionRow

__all__ = ["session_visibility", "visible_to"]


def _tenant_level(tenant_id: str, principal_id: str | None) -> bool:
    return principal_id is None or principal_id == tenant_id


def visible_to(owner: str | None, *, tenant_id: str, principal_id: str | None) -> bool:
    """Whether a session owned by ``owner`` (in ``tenant_id``) is visible to the caller."""
    return _tenant_level(tenant_id, principal_id) or owner in (None, tenant_id, principal_id)


def session_visibility(tenant_id: str, principal_id: str | None) -> ColumnElement[bool]:
    """The same rule as a filter on ``SessionRow`` (the tenant filter is separate)."""
    if _tenant_level(tenant_id, principal_id):
        return SessionRow.tenant_id == tenant_id
    return or_(
        SessionRow.owner_principal_id.is_(None),
        SessionRow.owner_principal_id == tenant_id,
        SessionRow.owner_principal_id == principal_id,
    )
