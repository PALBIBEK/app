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

from typing import TYPE_CHECKING

from supervisor.catalog.core.errors import CatalogOwnershipRequired, CatalogPermissionDenied
from supervisor.catalog.core.types import ArtifactKind, CatalogGrant

if TYPE_CHECKING:
    from supervisor.catalog.core.protocols import ArtifactStoreProtocol


__all__ = [
    "CatalogAuthz",
]


class CatalogAuthz:
    """Authorization service for catalog operations.

    Implements CatalogAuthzProtocol.

    Supports:
    - Grant-based permissions (per-principal grants)
    - Ownership-based permissions (artifact creator/owner)
    - Admin override (admin principals can do anything)

    Attributes:
        _admin_principals: Set of principal IDs with admin access
        _grants: Dict mapping principal_id -> set of grants
        _artifact_store: Store for ownership lookups
    """

    def __init__(
        self,
        *,
        admin_principals: list[str] | None = None,
        artifact_store: ArtifactStoreProtocol | None = None,
    ) -> None:
        """Initialize the authorization service.

        Args:
            admin_principals: List of principal IDs with admin access
            artifact_store: Artifact store for ownership lookups
        """
        self._admin_principals: set[str] = set(admin_principals or [])
        self._grants: dict[str, set[CatalogGrant]] = {}
        self._artifact_store = artifact_store

    def grant(
        self,
        principal_id: str,
        grant: CatalogGrant,
    ) -> None:
        """Grant a permission to a principal.

        Args:
            principal_id: Principal to grant to
            grant: Permission to grant
        """
        if principal_id not in self._grants:
            self._grants[principal_id] = set()
        self._grants[principal_id].add(grant)

    def revoke(
        self,
        principal_id: str,
        grant: CatalogGrant,
    ) -> None:
        """Revoke a permission from a principal.

        Args:
            principal_id: Principal to revoke from
            grant: Permission to revoke
        """
        if principal_id in self._grants:
            self._grants[principal_id].discard(grant)

    def add_admin(self, principal_id: str) -> None:
        """Add a principal as admin.

        Args:
            principal_id: Principal to make admin
        """
        self._admin_principals.add(principal_id)

    def remove_admin(self, principal_id: str) -> None:
        """Remove a principal from admin.

        Args:
            principal_id: Principal to remove from admin
        """
        self._admin_principals.discard(principal_id)

    async def check(
        self,
        principal_id: str,
        grant: CatalogGrant,
        *,
        artifact_kind: ArtifactKind | None = None,
        artifact_id: str | None = None,
        artifact_owner: str | None = None,
    ) -> bool:
        """Check if principal has grant.

        Args:
            principal_id: Who to check
            grant: Required grant
            artifact_kind: Context (optional)
            artifact_id: Context (optional)
            artifact_owner: For ownership checks

        Returns:
            True if granted
        """
        # Admin always has access
        if principal_id in self._admin_principals:
            return True

        # Check explicit grant
        principal_grants = self._grants.get(principal_id, set())
        if grant in principal_grants:
            return True

        # Check admin grant implies all others
        if CatalogGrant.ADMIN in principal_grants:
            return True

        # Owner-based access for certain operations
        if artifact_owner is not None and artifact_owner == principal_id:
            # Owners can read, publish, and deprecate their own artifacts
            if grant in (
                CatalogGrant.ARTIFACT_READ,
                CatalogGrant.ARTIFACT_PUBLISH,
                CatalogGrant.ARTIFACT_DEPRECATE,
                CatalogGrant.ANALYTICS_VIEW,
            ):
                return True

        return False

    async def require(
        self,
        principal_id: str,
        grant: CatalogGrant,
        *,
        artifact_kind: ArtifactKind | None = None,
        artifact_id: str | None = None,
        artifact_owner: str | None = None,
    ) -> None:
        """Require grant, raise if missing.

        Args:
            principal_id: Who to check
            grant: Required grant
            artifact_kind: Context (optional)
            artifact_id: Context (optional)
            artifact_owner: For ownership checks

        Raises:
            CatalogPermissionDenied: If grant not held
        """
        has_grant = await self.check(
            principal_id,
            grant,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            artifact_owner=artifact_owner,
        )

        if not has_grant:
            raise CatalogPermissionDenied(
                principal_id=principal_id,
                required_grant=grant.value,
                artifact_kind=artifact_kind.value if artifact_kind else None,
                artifact_id=artifact_id,
            )

    async def is_owner(
        self,
        principal_id: str,
        artifact_kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> bool:
        """Check if principal owns artifact.

        Args:
            principal_id: Who to check
            artifact_kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Returns:
            True if owner
        """
        if self._artifact_store is None:
            return False

        metadata = await self._artifact_store.get_metadata(
            artifact_kind, artifact_id, digest
        )
        if metadata is None:
            return False

        return metadata.owner_id == principal_id

    async def is_admin(
        self,
        principal_id: str,
    ) -> bool:
        """Check if principal has admin grant.

        Args:
            principal_id: Who to check

        Returns:
            True if admin
        """
        if principal_id in self._admin_principals:
            return True

        principal_grants = self._grants.get(principal_id, set())
        return CatalogGrant.ADMIN in principal_grants

    async def require_owner_or_admin(
        self,
        principal_id: str,
        artifact_kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> None:
        """Require principal is owner or admin.

        Args:
            principal_id: Who to check
            artifact_kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Raises:
            CatalogOwnershipRequired: If not owner and not admin
        """
        if await self.is_admin(principal_id):
            return

        if await self.is_owner(principal_id, artifact_kind, artifact_id, digest):
            return

        # Get actual owner for error message
        owner_id: str | None = None
        if self._artifact_store:
            metadata = await self._artifact_store.get_metadata(
                artifact_kind, artifact_id, digest
            )
            if metadata:
                owner_id = metadata.owner_id

        raise CatalogOwnershipRequired(
            principal_id=principal_id,
            artifact_kind=artifact_kind.value,
            artifact_id=artifact_id,
            owner_id=owner_id,
        )
