"""Protocol interfaces for harness subsystem - dependency injection.

All harness components defined as Protocol classes to enable:
- Dependency injection in FastAPI
- Easy mocking in tests
- Swappable implementations
- Clear interface contracts

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.harness.core.types import (
        HarnessManifest,
        RuntimeCapabilities,
    )

__all__ = [
    "CompatibilityCheckerProtocol",
    "HarnessResolverProtocol",
]


@runtime_checkable
class HarnessResolverProtocol(Protocol):
    """Interface for harness version resolution.

    Implementations must provide version resolution with caching,
    status checking, and multi-source fallback (DB, catalog, directory).
    """

    async def resolve(
        self,
        harness_version: str,
        *,
        for_create: bool = False,
    ) -> HarnessManifest:
        """Resolve version string to frozen manifest.

        Args:
            harness_version: Exact version string (never "latest")
            for_create: If True, require active status

        Returns:
            Frozen harness manifest

        Raises:
            UnknownHarness: If version not found
            HarnessNotOffered: If for_create and status != active
        """
        ...

    async def list_available(
        self,
        *,
        include_deprecated: bool = False,
    ) -> list[str]:
        """List available harness versions.

        Args:
            include_deprecated: Include deprecated versions

        Returns:
            Sorted list of version strings
        """
        ...

    async def publish(
        self,
        manifest: HarnessManifest,
        *,
        status: str = "active",
    ) -> None:
        """Publish a new harness version.

        Args:
            manifest: Manifest to publish
            status: Initial status (active/draft)
        """
        ...


@runtime_checkable
class CompatibilityCheckerProtocol(Protocol):
    """Interface for harness compatibility checking.

    Implementations must validate manifests against the current
    runtime environment.
    """

    def check(
        self,
        manifest: HarnessManifest,
        *,
        sandbox_available: bool = False,
    ) -> None:
        """Check harness compatibility with current environment.

        Args:
            manifest: Manifest to check
            sandbox_available: Whether sandbox provider is configured

        Raises:
            IncompatibleHarness: If incompatible
            UnsupportedProtocol: If protocol not supported
            MissingRuntimeCapability: If runtime lacks capability
            SandboxRequired: If sandbox required but not available
        """
        ...

    def get_capabilities(self, runtime_name: str) -> RuntimeCapabilities:
        """Get declared capabilities for a runtime.

        Args:
            runtime_name: Name of runtime

        Returns:
            Runtime capabilities

        Raises:
            IncompatibleHarness: If runtime unknown
        """
        ...


