"""Catalog authorization service — permission checking for catalog operations.

Implements CatalogAuthzProtocol with:
- Grant-based access control
- Owner-based permissions
- Admin override capability

Design principles:
- Explicit grants over implicit permissions
- Owner can always manage their own artifacts
- Admin bypass for operational needs

Reference: policy/sandbox_policy.py pattern.
"""

from __future__ import annotations

from supervisor.catalog.core.types import CatalogGrant

__all__ = [
]

_OWNER_GRANTS = frozenset({
    CatalogGrant.ARTIFACT_READ,
    CatalogGrant.ARTIFACT_PUBLISH,
    CatalogGrant.ARTIFACT_DEPRECATE,
    CatalogGrant.ANALYTICS_VIEW,
})


