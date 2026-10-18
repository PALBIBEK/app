"""Harness exception hierarchy with structured error data.

Design principles:
- Rich context in exception attributes
- No bare Exception raises
- Retryable flag for transient failures
- Structured error codes for API responses
- to_dict() for serialization

Reference: catalog/core/errors.py pattern.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    # Base
    "HarnessError",
    # Resolution
    "UnknownHarness",
    "HarnessNotOffered",
    "VersionResolutionError",
    # Manifest
    "ManifestError",
    "ManifestParseError",
    "ManifestValidationError",
    "InvalidDigest",
    # Compatibility
    "IncompatibleHarness",
    "UnsupportedProtocol",
    "MissingRuntimeCapability",
    "SandboxRequired",
    "ApprovalsNotSupported",
    # Spawn
    "SpawnError",
    "MissingHarnessCli",
    "InvalidGatewayConfig",
    # Security
    "PathTraversalAttempt",
    "InvalidEnvironmentValue",
]


# =============================================================================
# Base Exception
# =============================================================================


class HarnessError(Exception):
    """Base exception for all harness errors.

    Attributes:
        error_code: Machine-readable code for API responses
        retryable: Whether the operation may succeed on retry
        detail: Structured context for debugging
    """

    error_code: str = "HARNESS_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "error_code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "detail": self.detail,
        }


# =============================================================================
# Resolution Errors
# =============================================================================


class UnknownHarness(HarnessError):
    """Requested harness version does not exist.

    This error is raised when a harness version string cannot be
    resolved to a manifest.
    """

    error_code = "UNKNOWN_HARNESS"

    def __init__(self, harness_version: str) -> None:
        super().__init__(
            f"unknown harness_version={harness_version!r}",
            detail={"harness_version": harness_version},
        )
        self.harness_version = harness_version


class HarnessNotOffered(HarnessError):
    """Harness exists but is not offered for new sessions.

    This error is raised when a harness version exists but has
    a status that prevents new session creation (draft/deprecated).
    """

    error_code = "HARNESS_NOT_OFFERED"

    def __init__(self, *, harness_version: str, status: str) -> None:
        super().__init__(
            f"harness_version={harness_version!r} status={status!r} is not offered for new sessions",
            detail={"harness_version": harness_version, "status": status},
        )
        self.harness_version = harness_version
        self.status = status


class VersionResolutionError(HarnessError):
    """Failed to resolve harness version.

    This is a transient error that may succeed on retry.
    """

    error_code = "VERSION_RESOLUTION_ERROR"
    retryable = True

    def __init__(self, harness_version: str, reason: str) -> None:
        super().__init__(
            f"failed to resolve harness_version={harness_version!r}: {reason}",
            detail={"harness_version": harness_version, "reason": reason},
        )
        self.harness_version = harness_version
        self.reason = reason


# =============================================================================
# Manifest Errors
# =============================================================================


class ManifestError(HarnessError):
    """Base for manifest parsing/validation errors."""

    error_code = "MANIFEST_ERROR"


class ManifestParseError(ManifestError):
    """Failed to parse YAML manifest.

    This error is raised when the manifest YAML is malformed.
    """

    error_code = "MANIFEST_PARSE_ERROR"

    def __init__(self, reason: str, *, source: str | None = None) -> None:
        super().__init__(
            f"manifest parse error: {reason}",
            detail={"reason": reason, "source": source},
        )
        self.reason = reason
        self.source = source


class ManifestValidationError(ManifestError):
    """Manifest structure is invalid.

    This error is raised when a manifest field has an invalid value.
    """

    error_code = "MANIFEST_VALIDATION_ERROR"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(
            f"manifest validation error: {field}: {reason}",
            detail={"field": field, "reason": reason},
        )
        self.field = field
        self.reason = reason


class InvalidDigest(ManifestError):
    """Digest format is invalid.

    Expected format: sha256:<64 hex chars>
    """

    error_code = "INVALID_DIGEST"

    def __init__(self, digest: str, reason: str) -> None:
        super().__init__(
            f"invalid digest {digest!r}: {reason}",
            detail={"digest": digest, "reason": reason},
        )
        self.digest = digest
        self.reason = reason


# =============================================================================
# Compatibility Errors
# =============================================================================


class IncompatibleHarness(HarnessError):
    """Harness is incompatible with current runtime/environment.

    Base class for specific incompatibility errors.
    """

    error_code = "INCOMPATIBLE_HARNESS"

    def __init__(self, reason: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(reason, detail=detail)
        self.reason = reason


class UnsupportedProtocol(IncompatibleHarness):
    """Protocol version not supported.

    This error is raised when the manifest protocol version
    is not in the set of supported versions.
    """

    error_code = "UNSUPPORTED_PROTOCOL"

    def __init__(self, *, requested: int, supported: list[int]) -> None:
        super().__init__(
            f"unsupported protocol_version={requested}; supported={supported}",
            detail={"requested": requested, "supported": supported},
        )
        self.requested = requested
        self.supported = supported


class MissingRuntimeCapability(IncompatibleHarness):
    """Runtime lacks required capability.

    This error is raised when a runtime does not support
    a required command or feature.
    """

    error_code = "MISSING_RUNTIME_CAPABILITY"

    def __init__(self, *, runtime: str, missing: list[str]) -> None:
        super().__init__(
            f"runtime {runtime!r} missing capabilities: {missing}",
            detail={"runtime": runtime, "missing": missing},
        )
        self.runtime = runtime
        self.missing = missing


class SandboxRequired(IncompatibleHarness):
    """Harness requires sandbox but none configured.

    This error is raised when a manifest has requires_sandbox=true
    but no sandbox provider is available.
    """

    error_code = "SANDBOX_REQUIRED"

    def __init__(self, harness_version: str) -> None:
        super().__init__(
            f"harness {harness_version!r} requires sandbox but none configured",
            detail={"harness_version": harness_version},
        )
        self.harness_version = harness_version


class ApprovalsNotSupported(IncompatibleHarness):
    """Harness requires approvals but runtime does not support them.

    This error is raised when a manifest requires approvals
    but the runtime does not have approval support.
    """

    error_code = "APPROVALS_NOT_SUPPORTED"

    def __init__(self, *, harness_version: str, runtime: str) -> None:
        super().__init__(
            f"harness {harness_version!r} requires approvals but runtime {runtime!r} does not support them",
            detail={"harness_version": harness_version, "runtime": runtime},
        )
        self.harness_version = harness_version
        self.runtime = runtime


# =============================================================================
# Spawn Errors
# =============================================================================


class SpawnError(HarnessError):
    """Failed to build spawn plan.

    Base class for spawn-related errors.
    """

    error_code = "SPAWN_ERROR"


class MissingHarnessCli(SpawnError):
    """HARNESS_CLI not configured.

    This error is raised when the harness runtime is requested
    but no CLI path is available.
    """

    error_code = "MISSING_HARNESS_CLI"

    def __init__(self) -> None:
        super().__init__(
            "harness runtime requires HARNESS_CLI or .yal/launch.json cli entry",
            detail={},
        )


class InvalidGatewayConfig(SpawnError):
    """Gateway configuration is invalid.

    This error is raised when plug_bindings.gateway_base_url
    is set but the gateway is not properly configured.
    """

    error_code = "INVALID_GATEWAY_CONFIG"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"invalid gateway configuration: {reason}",
            detail={"reason": reason},
        )
        self.reason = reason


# =============================================================================
# Security Errors
# =============================================================================


class PathTraversalAttempt(HarnessError):
    """Detected path traversal attempt.

    This error is raised when a path contains .. components
    or otherwise attempts to escape the allowed directory.
    """

    error_code = "PATH_TRAVERSAL_ATTEMPT"

    def __init__(self, path: str) -> None:
        super().__init__(
            f"path traversal detected: {path!r}",
            detail={"path": path},
        )
        self.path = path


class InvalidEnvironmentValue(HarnessError):
    """Environment variable value is invalid/unsafe.

    This error is raised when an environment variable contains
    control characters or other unsafe content.
    """

    error_code = "INVALID_ENVIRONMENT_VALUE"

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(
            f"invalid environment value for {key!r}: {reason}",
            detail={"key": key, "reason": reason},
        )
        self.key = key
        self.reason = reason
