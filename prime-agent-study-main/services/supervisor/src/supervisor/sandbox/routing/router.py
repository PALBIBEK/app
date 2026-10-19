"""Dynamic sandbox router with security guards.

This module implements QM's routing pattern:
- Wraps multiple backends behind a single interface
- Routes requests based on database-backed routes
- Falls back to default backend when no route exists
- Reports errors without throwing (graceful degradation)

Security guards (YOUR code) run on EVERY request:
1. Multi-tenant provider validation
2. Image digest validation (D14)
3. Environment sanitization

Reference: QM's sandbox-routing.ts pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ClassVar, Protocol, runtime_checkable

from supervisor.sandbox.base import Sandbox, SandboxProvider
from supervisor.sandbox.routing.capabilities import (
    PROVIDER_CAPABILITIES,
    SandboxCapability,
    aggregate_capabilities,
)
from supervisor.sandbox.security.env import sanitize_sandbox_env
from supervisor.sandbox.security.guards import (
    assert_multi_tenant_provider,
    is_placeholder_image_digest,
)
from supervisor.sandbox.resolve import PlaceholderImageError
from supervisor.sandbox.routing.routes import RouteStore, SandboxRoute

if TYPE_CHECKING:
    pass

__all__ = [
    "SandboxRouter",
    "SandboxRouterImpl",
    "create_sandbox_router",
    "RouterError",
    "BackendUnavailableError",
    "CapabilityUnsupportedError",
]


class RouterError(Exception):
    """Base error for router operations."""

    def __init__(
        self,
        message: str,
        *,
        scope_id: str | None = None,
        backend: str | None = None,
    ) -> None:
        super().__init__(message)
        self.scope_id = scope_id
        self.backend = backend


class BackendUnavailableError(RouterError):
    """Routed backend is not available in this deployment."""

    def __init__(self, backend: str, available: frozenset[str]) -> None:
        super().__init__(
            f"backend {backend!r} not available; have: {sorted(available)}",
            backend=backend,
        )
        self.available = available


class CapabilityUnsupportedError(RouterError):
    """Backend lacks required capability."""

    def __init__(self, backend: str, capability: str) -> None:
        super().__init__(
            f"backend {backend!r} does not support {capability!r}",
            backend=backend,
        )
        self.capability = capability


ErrorCallback = Callable[[str, str, Exception], None]


@runtime_checkable
class SandboxRouter(Protocol):
    """Protocol for sandbox routers.

    A router wraps multiple backends and routes requests to the appropriate one.
    """

    @property
    def default_backend(self) -> str:
        """Default backend when no route exists."""
        ...

    @property
    def available_backends(self) -> frozenset[str]:
        """Backends available in this deployment."""
        ...

    @property
    def capabilities(self) -> frozenset[SandboxCapability]:
        """Aggregate capabilities from all backends."""
        ...

    async def route_for(self, scope_id: str) -> SandboxRoute | None:
        """Get explicit route for scope, if any."""
        ...

    async def pick(self, scope_id: str) -> tuple[str, SandboxProvider]:
        """Select backend for scope. Returns (backend_name, provider)."""
        ...

    async def create(
        self,
        scope_id: str,
        *,
        image: str | None = None,
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        scratch: bool = False,
    ) -> Sandbox:
        """Create sandbox for scope using routed backend."""
        ...

    async def health_check(self) -> dict[str, bool]:
        """Check health of all backends."""
        ...


class SandboxRouterImpl:
    """Dynamic sandbox router with security guards.

    Wraps multiple backends and routes requests based on:
    1. Explicit route in database (if exists)
    2. Default backend (fallback)

    Security guards run on EVERY request:
    - Multi-tenant provider validation
    - Image digest validation (D14)
    - Environment sanitization

    Attributes:
        default_backend: Backend used when no route exists.
        available_backends: Set of backends in this deployment.
        capabilities: Aggregate capabilities from all backends.

    Example:
        >>> router = SandboxRouterImpl(
        ...     backends={"e2b": e2b_provider, "docker": docker_provider},
        ...     routes=route_store,
        ...     default_backend="e2b",
        ... )
        >>> sandbox = await router.create("scope-123")
    """

    ROUTE_CACHE_TTL_MS: ClassVar[int] = 15_000

    __slots__ = (
        "_backends",
        "_routes",
        "_default_backend",
        "_strict",
        "_on_error",
        "_reported_gaps",
        "_capabilities",
    )

    def __init__(
        self,
        *,
        backends: dict[str, SandboxProvider],
        routes: RouteStore,
        default_backend: str,
        multi_tenant_strict: bool = False,
        on_error: ErrorCallback | None = None,
    ) -> None:
        """Initialize router.

        Args:
            backends: Map of backend name to provider instance.
            routes: Route storage backend.
            default_backend: Backend to use when no route exists.
            multi_tenant_strict: Enable strict multi-tenant mode.
            on_error: Callback for error reporting (category, code, exception).

        Raises:
            ValueError: If default_backend not in backends.
        """
        if default_backend not in backends:
            raise ValueError(
                f"default_backend {default_backend!r} not in backends: "
                f"{sorted(backends.keys())}"
            )

        self._backends = backends
        self._routes = routes
        self._default_backend = default_backend
        self._strict = multi_tenant_strict
        self._on_error = on_error or (lambda *_: None)
        self._reported_gaps: set[tuple[str, str]] = set()
        self._capabilities = aggregate_capabilities(backends)

    @property
    def default_backend(self) -> str:
        """Default backend when no route exists."""
        return self._default_backend

    @property
    def available_backends(self) -> frozenset[str]:
        """Backends available in this deployment."""
        return frozenset(self._backends.keys())

    @property
    def capabilities(self) -> frozenset[SandboxCapability]:
        """Aggregate capabilities from all backends."""
        return self._capabilities

    def _report_error(self, category: str, code: str, error: Exception) -> None:
        """Report error via callback (doesn't throw)."""
        key = (category, code)
        if key not in self._reported_gaps:
            self._reported_gaps.add(key)
            self._on_error(category, code, error)

    async def route_for(self, scope_id: str) -> SandboxRoute | None:
        """Get explicit route for scope, if any.

        Uses RouteStore with caching (15-second TTL).

        Args:
            scope_id: Scope to look up.

        Returns:
            SandboxRoute if explicitly routed, None otherwise.
        """
        return await self._routes.get(scope_id)

    async def pick(self, scope_id: str) -> tuple[str, SandboxProvider]:
        """Select backend for scope.

        Resolution order:
        1. Explicit route in database
        2. Default backend

        If routed backend unavailable, falls back to default with error report.

        Args:
            scope_id: Scope to route.

        Returns:
            Tuple of (backend_name, provider).
        """
        route = await self._routes.get(scope_id)

        if route is not None:
            backend_name = route.backend

            if backend_name not in self._backends:
                self._report_error(
                    "sandbox_routing",
                    "backend_unavailable",
                    BackendUnavailableError(backend_name, self.available_backends),
                )
                backend_name = self._default_backend
        else:
            backend_name = self._default_backend

        provider = self._backends[backend_name]
        return backend_name, provider

    async def create(
        self,
        scope_id: str,
        *,
        image: str | None = None,
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        scratch: bool = False,
    ) -> Sandbox:
        """Create sandbox with full security guard chain.

        Guards (YOUR security - always run):
        1. Multi-tenant provider validation
        2. Image digest validation (D14)
        3. Environment sanitization

        Args:
            scope_id: Scope for the sandbox.
            image: Image digest (or use route/manifest default).
            env: Environment variables (will be sanitized).
            labels: Additional labels for the sandbox.
            scratch: If True, creates ephemeral sandbox (no route created).

        Returns:
            Created Sandbox instance.

        Raises:
            MultiTenantViolation: If provider violates strict mode.
            PlaceholderImageError: If image is a placeholder/fixture.
        """
        backend_name, provider = await self.pick(scope_id)

        assert_multi_tenant_provider(
            backend_name,
            multi_tenant_strict=self._strict,
        )

        route = await self._routes.get(scope_id)
        effective_image = (route.image_digest if route else None) or image

        if effective_image and is_placeholder_image_digest(effective_image):
            raise PlaceholderImageError(effective_image)

        safe_env = sanitize_sandbox_env(env)

        effective_labels = {
            "scope_id": scope_id,
            "backend": backend_name,
            **(labels or {}),
        }

        if scratch:
            effective_labels["scratch"] = "true"

        sandbox = await provider.create(
            image=effective_image,
            env=safe_env,
            labels=effective_labels,
        )

        return sandbox

    async def get(self, scope_id: str, sandbox_id: str) -> Sandbox | None:
        """Reattach to existing sandbox.

        Uses routed backend or default.

        Args:
            scope_id: Scope for routing.
            sandbox_id: ID of sandbox to reattach.

        Returns:
            Sandbox if found, None otherwise.
        """
        _, provider = await self.pick(scope_id)
        return await provider.get(sandbox_id)

    async def health_check(self) -> dict[str, bool]:
        """Check health of all backends.

        Returns:
            Dict of backend name to health status.
        """
        results: dict[str, bool] = {}
        for name, provider in self._backends.items():
            try:
                results[name] = await provider.health_check()
            except Exception:
                results[name] = False
        return results

    def has_capability(self, cap: SandboxCapability) -> bool:
        """Check if ANY backend has a capability.

        Args:
            cap: Capability to check.

        Returns:
            True if at least one backend supports it.
        """
        return cap in self._capabilities

    def backend_has_capability(self, backend: str, cap: SandboxCapability) -> bool:
        """Check if specific backend has capability.

        Args:
            backend: Backend name.
            cap: Capability to check.

        Returns:
            True if backend supports capability.
        """
        backend_caps = PROVIDER_CAPABILITIES.get(backend, frozenset())
        return cap in backend_caps


def create_sandbox_router(
    *,
    backends: dict[str, SandboxProvider],
    routes: RouteStore,
    default_backend: str,
    multi_tenant_strict: bool = False,
    on_error: ErrorCallback | None = None,
) -> SandboxRouter:
    """Factory for sandbox router.

    Creates a router with the given backends and configuration.
    Aggregates capabilities from all backends.

    Args:
        backends: Map of backend name to provider instance.
        routes: Route storage backend.
        default_backend: Backend to use when no route exists.
        multi_tenant_strict: Enable strict multi-tenant mode.
        on_error: Callback for error reporting.

    Returns:
        Configured SandboxRouter instance.

    Example:
        >>> router = create_sandbox_router(
        ...     backends={"e2b": e2b_provider},
        ...     routes=MemoryRouteStore(),
        ...     default_backend="e2b",
        ... )
    """
    return SandboxRouterImpl(
        backends=backends,
        routes=routes,
        default_backend=default_backend,
        multi_tenant_strict=multi_tenant_strict,
        on_error=on_error,
    )
