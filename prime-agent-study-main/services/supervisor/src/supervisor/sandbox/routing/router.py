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

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from supervisor.sandbox.base import Sandbox, SandboxProvider
from supervisor.sandbox.routing.capabilities import (
    SandboxCapability,
)
from supervisor.sandbox.routing.routes import SandboxRoute

__all__ = [
    "SandboxRouter",
]


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


