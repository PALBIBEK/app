"""Runtime compatibility checking service.

Validates harness manifests against the current runtime environment.
Checks protocol versions, runtime capabilities, and sandbox requirements.

Reference: Original compat.py refactored as service class.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from supervisor.harness.config import get_settings, HarnessSettings
from supervisor.harness.core.errors import (
    ApprovalsNotSupported,
    IncompatibleHarness,
    MissingRuntimeCapability,
    SandboxRequired,
    UnsupportedProtocol,
)
from supervisor.harness.core.types import HarnessManifest, RuntimeCapabilities

if TYPE_CHECKING:
    from supervisor.sandbox.base import SandboxProvider

__all__ = [
    "CompatibilityChecker",
    "capabilities_for",
    "load_supported_protocol_versions",
]

logger = logging.getLogger(__name__)

# Commands the supervisor requires of every runtime
_REQUIRED_COMMANDS: frozenset[str] = frozenset({
    "prompt",
    "abort",
    "get_state",
    "get_history",
})


def load_supported_protocol_versions(
    path: Path | str | None = None,
    *,
    settings: HarnessSettings | None = None,
) -> frozenset[int]:
    """Load supported protocol versions from config.

    Searches for protocol_versions.yaml in multiple locations:
    1. Explicit path argument
    2. YAL_CONTRACTS_DIR / CONTRACTS_DIR environment variable
    3. /contracts/protocol_versions.yaml (Docker mount)
    4. contracts/protocol_versions.yaml (local)
    5. Relative to this file (host checkout)

    Args:
        path: Explicit path to protocol_versions.yaml
        settings: Settings instance (uses get_settings() if None)

    Returns:
        Frozenset of supported protocol version integers
    """
    settings = settings or get_settings()

    if path is None:
        candidates: list[Path] = []

        # From settings/environment
        if settings.contracts_dir:
            candidates.append(Path(settings.contracts_dir) / "protocol_versions.yaml")

        # Docker mount location
        candidates.append(Path("/contracts/protocol_versions.yaml"))

        # Local location
        candidates.append(Path("contracts/protocol_versions.yaml"))

        # Relative to this file (host checkout)
        for parent in Path(__file__).resolve().parents:
            candidates.append(parent / "contracts" / "protocol_versions.yaml")

        for candidate in candidates:
            if candidate.is_file():
                path = candidate
                logger.debug(
                    "Found protocol versions file",
                    extra={"path": str(candidate)},
                )
                break
        else:
            # Fallback to default
            logger.debug("No protocol versions file found, using defaults")
            return settings.default_protocol_versions

    try:
        data = yaml.safe_load(Path(path).read_text())
        supported = data.get("supported") or []
        return frozenset(int(v) for v in supported)
    except Exception as exc:
        logger.warning(
            "Failed to load protocol versions",
            extra={"path": str(path), "error": str(exc)},
        )
        return settings.default_protocol_versions


def capabilities_for(runtime_name: str) -> RuntimeCapabilities:
    """Get declared capabilities for a runtime.

    Args:
        runtime_name: Name of the runtime

    Returns:
        RuntimeCapabilities for the runtime

    Raises:
        IncompatibleHarness: If runtime is unknown
    """
    if runtime_name == "echo":
        return RuntimeCapabilities(
            protocol_versions=frozenset({1, 2}),
            commands=frozenset(_REQUIRED_COMMANDS | {"set_auto_retry"}),
            emits_settled_event=False,
            supports_fork=False,
            supports_history=True,
            supports_approvals=True,
        )

    if runtime_name == "harness":
        return RuntimeCapabilities(
            protocol_versions=frozenset({1, 2}),
            commands=frozenset(
                _REQUIRED_COMMANDS | {"set_auto_retry", "extension_ui_response"}
            ),
            emits_settled_event=False,
            supports_fork=False,
            supports_history=True,
            supports_approvals=True,
        )

    if runtime_name == "githi":
        return RuntimeCapabilities(
            protocol_versions=frozenset({1, 2}),
            commands=frozenset(_REQUIRED_COMMANDS),
            emits_settled_event=False,
            supports_fork=False,
            supports_history=True,
            supports_approvals=True,
        )

    raise IncompatibleHarness(
        f"unregistered runtime {runtime_name!r}",
        detail={"runtime": runtime_name},
    )


class CompatibilityChecker:
    """Service for checking harness compatibility with runtime environment.

    Validates:
    - Protocol version support
    - Runtime registration
    - Required commands
    - Sandbox requirements
    - Approval support
    """

    def __init__(
        self,
        *,
        settings: HarnessSettings | None = None,
        supported_protocols: frozenset[int] | None = None,
        required_commands: frozenset[str] | None = None,
    ) -> None:
        """Initialize checker.

        Args:
            settings: Settings instance (uses get_settings() if None)
            supported_protocols: Override supported protocols (uses file if None)
            required_commands: Override required commands (uses default if None)
        """
        self._settings = settings or get_settings()
        self._supported_protocols = supported_protocols
        self._required_commands = required_commands or _REQUIRED_COMMANDS
        self._logger = logging.getLogger(__name__)

    def get_supported_protocols(self) -> frozenset[int]:
        """Get supported protocol versions.

        Returns:
            Frozenset of supported protocol version integers
        """
        if self._supported_protocols is not None:
            return self._supported_protocols
        return load_supported_protocol_versions(settings=self._settings)

    def get_capabilities(self, runtime_name: str) -> RuntimeCapabilities:
        """Get declared capabilities for a runtime.

        Args:
            runtime_name: Name of the runtime

        Returns:
            RuntimeCapabilities for the runtime

        Raises:
            IncompatibleHarness: If runtime is unknown
        """
        return capabilities_for(runtime_name)

    def check(
        self,
        manifest: HarnessManifest,
        *,
        sandbox_available: bool = False,
        sandbox_provider: SandboxProvider | None = None,
        runtime_caps: RuntimeCapabilities | None = None,
    ) -> None:
        """Check harness compatibility with current environment.

        Args:
            manifest: Manifest to check
            sandbox_available: Whether sandbox is available
            sandbox_provider: Sandbox provider instance (alternative to sandbox_available)
            runtime_caps: Override runtime capabilities

        Raises:
            UnsupportedProtocol: If protocol version not supported
            IncompatibleHarness: If runtime unknown
            MissingRuntimeCapability: If runtime lacks required commands
            SandboxRequired: If sandbox required but not available
            ApprovalsNotSupported: If approvals required but not supported
        """
        # Import here to avoid circular import
        from supervisor.agent.factory import known_runtime_names

        # Check protocol version
        protocols = self.get_supported_protocols()
        if manifest.protocol_version not in protocols:
            self._logger.warning(
                "Unsupported protocol version",
                extra={
                    "harness_version": manifest.harness_version,
                    "protocol_version": manifest.protocol_version,
                    "supported": sorted(protocols),
                },
            )
            raise UnsupportedProtocol(
                requested=manifest.protocol_version,
                supported=sorted(protocols),
            )

        # Check runtime registration
        if manifest.runtime not in known_runtime_names():
            self._logger.warning(
                "Unregistered runtime",
                extra={
                    "harness_version": manifest.harness_version,
                    "runtime": manifest.runtime,
                },
            )
            raise IncompatibleHarness(
                f"unregistered runtime {manifest.runtime!r}",
                detail={"runtime": manifest.runtime},
            )

        # Check runtime capabilities
        caps = runtime_caps or self.get_capabilities(manifest.runtime)
        missing = sorted(self._required_commands - caps.commands)
        if missing:
            self._logger.warning(
                "Missing runtime capabilities",
                extra={
                    "harness_version": manifest.harness_version,
                    "runtime": manifest.runtime,
                    "missing": missing,
                },
            )
            raise MissingRuntimeCapability(
                runtime=manifest.runtime,
                missing=missing,
            )

        # Check sandbox requirement
        has_sandbox = sandbox_available or sandbox_provider is not None
        if manifest.requires_sandbox and not has_sandbox:
            self._logger.warning(
                "Sandbox required but not available",
                extra={
                    "harness_version": manifest.harness_version,
                },
            )
            raise SandboxRequired(manifest.harness_version)

        # Check approvals requirement
        if (
            manifest.approvals is not None
            and manifest.approvals.required
            and not caps.supports_approvals
        ):
            self._logger.warning(
                "Approvals required but not supported",
                extra={
                    "harness_version": manifest.harness_version,
                    "runtime": manifest.runtime,
                },
            )
            raise ApprovalsNotSupported(
                harness_version=manifest.harness_version,
                runtime=manifest.runtime,
            )

        self._logger.debug(
            "Compatibility check passed",
            extra={"harness_version": manifest.harness_version},
        )


# Module-level convenience function for backward compatibility
def check(
    manifest: HarnessManifest,
    runtime_caps: RuntimeCapabilities | None = None,
    *,
    supported_protocols: frozenset[int] | None = None,
    sandbox_provider: SandboxProvider | None = None,
    required_commands: frozenset[str] = _REQUIRED_COMMANDS,
) -> None:
    """Check harness compatibility (convenience function).

    Args:
        manifest: Manifest to check
        runtime_caps: Override runtime capabilities
        supported_protocols: Override supported protocols
        sandbox_provider: Sandbox provider instance
        required_commands: Required commands

    Raises:
        Various IncompatibleHarness subclasses
    """
    checker = CompatibilityChecker(
        supported_protocols=supported_protocols,
        required_commands=required_commands,
    )
    checker.check(
        manifest,
        runtime_caps=runtime_caps,
        sandbox_provider=sandbox_provider,
    )
