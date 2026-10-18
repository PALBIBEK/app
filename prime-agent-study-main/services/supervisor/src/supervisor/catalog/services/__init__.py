"""Catalog service layer — high-level orchestration with observability.

Services:
- CatalogService: Main orchestrator for publish/deprecate/resolve with audit
- CatalogAuthz: Permission checking for catalog operations
- CompositionResolver: Resolve compositions to session pins

All services use Protocol interfaces for dependency injection.

Example:
    from supervisor.catalog.services import (
        CatalogService,
        CatalogAuthz,
        CompositionResolver,
    )

    # Create services with dependencies
    authz = CatalogAuthz(admin_principals=["system"])
    resolver = CompositionResolver(artifact_store)
    catalog = CatalogService(
        artifact_store=artifact_store,
        usage_store=usage_store,
        authz=authz,
        audit_logger=audit_logger,
    )

    # Publish artifact
    published = await catalog.publish(principal_id, artifact)
"""

from supervisor.catalog.services.authz import CatalogAuthz
from supervisor.catalog.services.catalog_service import CatalogService
from supervisor.catalog.services.resolver import CompositionResolver

__all__ = [
    "CatalogService",
    "CatalogAuthz",
    "CompositionResolver",
]
