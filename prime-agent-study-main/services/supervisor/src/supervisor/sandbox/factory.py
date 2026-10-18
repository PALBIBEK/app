"""Sandbox provider factory - the single source of truth for provider names.

This module is the ONLY place where provider name strings are mapped to
implementations. All provider instantiation should go through this factory.

Environment Configuration:
    SANDBOX_PROVIDER: Default provider name (none|local_process|local_docker|e2b)

Provider Selection Guide:
    - "none": No isolation, for harnesses with requires_sandbox=false
    - "local_process": Development, temp directory per sandbox
    - "local_docker": Production-local, Docker containers
    - "e2b": Production-cloud, E2B managed sandboxes
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from supervisor.sandbox.base import SandboxProvider

if TYPE_CHECKING:
    from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator


class ProviderName(StrEnum):
    """Canonical provider names."""

    NONE = "none"
    LOCAL_PROCESS = "local_process"
    LOCAL_DOCKER = "local_docker"
    E2B = "e2b"


# Aliases for flexibility
_PROVIDER_ALIASES: dict[str, ProviderName] = {
    "none": ProviderName.NONE,
    "null": ProviderName.NONE,
    "": ProviderName.NONE,
    "local_process": ProviderName.LOCAL_PROCESS,
    "local": ProviderName.LOCAL_PROCESS,
    "process": ProviderName.LOCAL_PROCESS,
    "local_docker": ProviderName.LOCAL_DOCKER,
    "docker": ProviderName.LOCAL_DOCKER,
    "e2b": ProviderName.E2B,
}


class UnknownProviderError(ValueError):
    """Raised when an unknown provider name is requested."""

    def __init__(self, name: str) -> None:
        valid = sorted(set(_PROVIDER_ALIASES.keys()) - {""})
        super().__init__(
            f"unknown sandbox provider {name!r}; "
            f"valid providers: {', '.join(valid)}"
        )
        self.name = name


def resolve_provider_name(name: str | None = None) -> ProviderName:
    """Resolve provider name from argument or environment.

    Args:
        name: Explicit provider name, or None to read from SANDBOX_PROVIDER.

    Returns:
        Canonical ProviderName enum value.

    Raises:
        UnknownProviderError: If name is not recognized.
    """
    raw = (name or os.environ.get("SANDBOX_PROVIDER") or "none").strip().lower()
    canonical = _PROVIDER_ALIASES.get(raw)
    if canonical is None:
        raise UnknownProviderError(raw)
    return canonical


def create_sandbox_provider(
    name: str | None = None,
    *,
    client: Any = None,
    hydrator: WorkspaceHydrator | None = None,
) -> SandboxProvider:
    """Create a sandbox provider by name.

    This is the factory entry point. All sandbox provider instantiation
    should go through this function.

    Args:
        name: Provider name (none|local_process|local_docker|e2b).
            If None, reads from SANDBOX_PROVIDER env var.
        client: Provider-specific client (e.g., Docker client).
            Passed to providers that need it.
        hydrator: WorkspaceHydrator for Track B workspace sync.
            Forwarded to providers that support it (local_process, local_docker).

    Returns:
        SandboxProvider instance ready to create sandboxes.

    Raises:
        UnknownProviderError: If provider name is not recognized.
        ModuleNotFoundError: If provider dependencies are not installed.

    Example:
        # From environment
        provider = create_sandbox_provider()

        # Explicit provider
        provider = create_sandbox_provider("local_docker")

        # With workspace sync
        provider = create_sandbox_provider("local_process", hydrator=hydrator)
    """
    canonical = resolve_provider_name(name)

    if canonical == ProviderName.NONE:
        from supervisor.sandbox.providers.none import NoneProvider
        return NoneProvider()

    if canonical == ProviderName.LOCAL_PROCESS:
        from supervisor.sandbox.providers.local_process import LocalProcessProvider
        return LocalProcessProvider(hydrator=hydrator)

    if canonical == ProviderName.LOCAL_DOCKER:
        from supervisor.sandbox.providers.local_docker import LocalDockerProvider
        return LocalDockerProvider(client=client, hydrator=hydrator)

    if canonical == ProviderName.E2B:
        from supervisor.sandbox.providers.e2b import E2BProvider
        return E2BProvider(client=client)

    # Should be unreachable due to enum exhaustiveness
    raise UnknownProviderError(str(canonical))


def get_default_provider_name() -> ProviderName:
    """Get the default provider name from environment.

    Returns:
        ProviderName from SANDBOX_PROVIDER env var, or NONE if not set.
    """
    return resolve_provider_name(None)


def is_isolation_provider(name: str | ProviderName) -> bool:
    """Check if provider provides actual isolation.

    Args:
        name: Provider name to check.

    Returns:
        True if provider isolates code execution (not "none").
    """
    try:
        canonical = resolve_provider_name(str(name))
        return canonical != ProviderName.NONE
    except UnknownProviderError:
        return False


def discover_backends(
    *,
    client: Any = None,
    hydrator: WorkspaceHydrator | None = None,
) -> dict[str, SandboxProvider]:
    """Discover available backends based on configuration.

    Creates providers for all configured backends. Used by the router
    to get the set of available backends.

    Args:
        client: Provider-specific client (e.g., Docker client).
        hydrator: WorkspaceHydrator for Track B workspace sync.

    Returns:
        Dict of backend name to provider instance.
    """
    backends: dict[str, SandboxProvider] = {}

    from supervisor.sandbox.providers.none import NoneProvider
    backends["none"] = NoneProvider()

    from supervisor.sandbox.providers.local_process import LocalProcessProvider
    backends["local_process"] = LocalProcessProvider(hydrator=hydrator)

    try:
        from supervisor.sandbox.providers.local_docker import LocalDockerProvider
        backends["local_docker"] = LocalDockerProvider(client=client, hydrator=hydrator)
    except ImportError:
        pass

    try:
        e2b_key = os.environ.get("E2B_API_KEY", "")
        if e2b_key:
            from supervisor.sandbox.providers.e2b import E2BProvider
            backends["e2b"] = E2BProvider(client=client)
    except ImportError:
        pass

    return backends


def create_router(
    *,
    route_store: Any | None = None,
    backends: dict[str, SandboxProvider] | None = None,
    default_backend: str | None = None,
    multi_tenant_strict: bool | None = None,
    session_factory: Any | None = None,
    hydrator: WorkspaceHydrator | None = None,
    on_error: Any | None = None,
) -> Any:
    """Create sandbox router with all dependencies.

    This is the main factory for creating a fully-configured router.
    Resolution order for each parameter:
    - route_store: explicit > postgres (if session_factory) > memory
    - backends: explicit > auto-discover
    - default_backend: explicit > SANDBOX_PROVIDER env > "none"
    - multi_tenant_strict: explicit > MULTI_TENANT_STRICT env > False

    Args:
        route_store: Route storage backend.
        backends: Map of backend name to provider.
        default_backend: Default backend for unrouted scopes.
        multi_tenant_strict: Enable strict multi-tenant mode.
        session_factory: SQLAlchemy session factory for postgres stores.
        hydrator: WorkspaceHydrator for workspace sync.
        on_error: Error callback for router.

    Returns:
        Configured SandboxRouter instance.

    Example:
        # Simple setup with memory store
        router = create_router()

        # Production with postgres
        router = create_router(session_factory=db_session_factory)

        # Strict multi-tenant mode
        router = create_router(multi_tenant_strict=True)
    """
    from supervisor.sandbox.routing.route_store import MemoryRouteStore, PostgresRouteStore
    from supervisor.sandbox.routing.router import SandboxRouterImpl

    if route_store is None:
        if session_factory is not None:
            route_store = PostgresRouteStore(session_factory)
        else:
            route_store = MemoryRouteStore()

    if backends is None:
        backends = discover_backends(hydrator=hydrator)

    if default_backend is None:
        default_backend = str(get_default_provider_name())

    if default_backend not in backends:
        default_backend = "none"

    if multi_tenant_strict is None:
        env_strict = os.environ.get("MULTI_TENANT_STRICT", "").lower()
        multi_tenant_strict = env_strict in ("1", "true", "yes")

    return SandboxRouterImpl(
        backends=backends,
        routes=route_store,
        default_backend=default_backend,
        multi_tenant_strict=multi_tenant_strict,
        on_error=on_error,
    )


def create_migration_runner(
    *,
    backends: dict[str, SandboxProvider] | None = None,
    route_store: Any | None = None,
    default_backend: str | None = None,
    advisory_lock: Any | None = None,
    session_factory: Any | None = None,
    hydrator: WorkspaceHydrator | None = None,
    has_live_work: Any | None = None,
) -> Any:
    """Create sandbox migration runner.

    Args:
        backends: Map of backend name to provider.
        route_store: Route storage backend.
        default_backend: Default backend for unrouted scopes.
        advisory_lock: Lock for serializing migrations.
        session_factory: SQLAlchemy session factory.
        hydrator: WorkspaceHydrator for workspace sync.
        has_live_work: Callback to check for live background work.

    Returns:
        Configured SandboxMigrationRunner instance.
    """
    from supervisor.sandbox.routing.advisory_lock import AdvisoryLock, MemoryAdvisoryLock
    from supervisor.sandbox.routing.route_store import MemoryRouteStore, PostgresRouteStore
    from supervisor.sandbox.routing.sandbox_migration import SandboxMigrationRunner

    if route_store is None:
        if session_factory is not None:
            route_store = PostgresRouteStore(session_factory)
        else:
            route_store = MemoryRouteStore()

    if backends is None:
        backends = discover_backends(hydrator=hydrator)

    if default_backend is None:
        default_backend = str(get_default_provider_name())

    if default_backend not in backends:
        default_backend = "none"

    if advisory_lock is None:
        if session_factory is not None:
            advisory_lock = AdvisoryLock(session_factory)
        else:
            advisory_lock = MemoryAdvisoryLock()

    return SandboxMigrationRunner(
        backends=backends,
        routes=route_store,
        default_backend=default_backend,
        advisory_lock=advisory_lock,
        has_live_work=has_live_work,
    )
