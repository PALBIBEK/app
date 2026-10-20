"""Protocol interfaces for harness subsystem - dependency injection.

All harness components defined as Protocol classes to enable:
- Dependency injection in FastAPI
- Easy mocking in tests
- Swappable implementations
- Clear interface contracts

Reference: catalog/core/protocols.py pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.catalog.core.types import MemoryProfile, SandboxProfile, SessionPins
    from supervisor.harness.core.types import (
        HarnessManifest,
        RuntimeCapabilities,
        SessionFilesPaths,
        SpawnPlan,
    )

__all__ = [
    "CompatibilityCheckerProtocol",
    "HarnessResolverProtocol",
    "ManifestParserProtocol",
    "SessionFilesBinderProtocol",
    "SpawnPlannerProtocol",
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


@runtime_checkable
class SpawnPlannerProtocol(Protocol):
    """Interface for building harness spawn plans.

    Implementations must build CLI arguments and environment
    for harness subprocess execution.
    """

    def build(
        self,
        *,
        session_dir: str | None,
        model: str | None,
        provider: str | None,
        seams: RuntimeSeams | None,
    ) -> SpawnPlan:
        """Build spawn plan for harness subprocess.

        Args:
            session_dir: Session directory path
            model: Model name override
            provider: Provider name override
            seams: Runtime seams for gateway binding

        Returns:
            Spawn plan with argv and env

        Raises:
            MissingHarnessCli: If CLI not configured
            InvalidGatewayConfig: If gateway config invalid
        """
        ...


@runtime_checkable
class SessionFilesBinderProtocol(Protocol):
    """Interface for materializing session files.

    Implementations must create .yal/ directory with all
    necessary configuration files.
    """

    def materialize(
        self,
        session_dir: Path | str,
        pins: SessionPins,
        seams: RuntimeSeams,
        *,
        cwd: str | None = None,
        sandbox_profile: SandboxProfile | None = None,
        memory_profile: MemoryProfile | None = None,
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> SessionFilesPaths:
        """Materialize .yal/ session files.

        Args:
            session_dir: Root session directory
            pins: Session artifact pins
            seams: Runtime seams for prompt/tools
            cwd: Working directory override
            sandbox_profile: Optional sandbox profile
            memory_profile: Optional memory profile
            session_id: Session identifier
            tenant_id: Tenant identifier

        Returns:
            Paths to created files

        Raises:
            PathTraversalAttempt: If path traversal detected
        """
        ...


@runtime_checkable
class ManifestParserProtocol(Protocol):
    """Interface for manifest parsing and validation.

    Implementations must parse YAML and validate manifest structure.
    """

    def parse(self, raw: str | bytes) -> dict[str, Any]:
        """Parse YAML manifest to dict.

        Args:
            raw: Raw YAML bytes or string

        Returns:
            Parsed dict

        Raises:
            ManifestParseError: If YAML invalid
        """
        ...

    def validate_and_freeze(self, data: dict[str, Any]) -> HarnessManifest:
        """Validate and convert to frozen manifest.

        Args:
            data: Parsed manifest dict

        Returns:
            Frozen manifest

        Raises:
            ManifestValidationError: If validation fails
        """
        ...
