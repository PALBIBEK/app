"""Core harness types, enums, errors, and protocols.

This module provides the foundational types for the harness subsystem:
- Enums: HarnessStatus, RuntimeMode, ToolClass, RecoveryAction, etc.
- Types: HarnessManifest, SpawnPlan, RuntimeCapabilities, etc.
- Errors: HarnessError hierarchy with structured error data
- Protocols: Interface definitions for DI and testing
- Digests: Content hashing utilities
"""

from __future__ import annotations

from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    HarnessStatus,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
)
from supervisor.harness.core.types import (
    ApprovalSpec,
    BudgetSpec,
    GuardSpec,
    HarnessManifest,
    RecoveryPolicy,
    RuntimeCapabilities,
    SessionFilesPaths,
    SpawnPlan,
    SubagentsSpec,
    ToolSpec,
)
from supervisor.harness.core.errors import (
    HarnessError,
    HarnessNotOffered,
    IncompatibleHarness,
    InvalidDigest,
    InvalidEnvironmentValue,
    ManifestError,
    ManifestParseError,
    ManifestValidationError,
    MissingHarnessCli,
    MissingRuntimeCapability,
    PathTraversalAttempt,
    SandboxRequired,
    SpawnError,
    InvalidGatewayConfig,
    UnknownHarness,
    UnsupportedProtocol,
)
from supervisor.harness.core.protocols import (
    CompatibilityCheckerProtocol,
    HarnessResolverProtocol,
    ManifestParserProtocol,
    SessionFilesBinderProtocol,
    SpawnPlannerProtocol,
)
from supervisor.harness.core.digests import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    content_digest,
    validate_digest_format,
    verify_digest,
)

__all__ = [
    # Enums
    "ApprovalOnTimeout",
    "GuardBehavior",
    "HarnessStatus",
    "RecoveryAction",
    "RuntimeMode",
    "ToolClass",
    # Types
    "ApprovalSpec",
    "BudgetSpec",
    "GuardSpec",
    "HarnessManifest",
    "RecoveryPolicy",
    "RuntimeCapabilities",
    "SessionFilesPaths",
    "SpawnPlan",
    "SubagentsSpec",
    "ToolSpec",
    # Errors
    "HarnessError",
    "HarnessNotOffered",
    "IncompatibleHarness",
    "InvalidDigest",
    "InvalidEnvironmentValue",
    "InvalidGatewayConfig",
    "ManifestError",
    "ManifestParseError",
    "ManifestValidationError",
    "MissingHarnessCli",
    "MissingRuntimeCapability",
    "PathTraversalAttempt",
    "SandboxRequired",
    "SpawnError",
    "UnknownHarness",
    "UnsupportedProtocol",
    # Protocols
    "CompatibilityCheckerProtocol",
    "HarnessResolverProtocol",
    "ManifestParserProtocol",
    "SessionFilesBinderProtocol",
    "SpawnPlannerProtocol",
    # Digests
    "DIGEST_PATTERN",
    "canonical_json_bytes",
    "content_digest",
    "validate_digest_format",
    "verify_digest",
]
