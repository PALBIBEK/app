"""Core harness types, enums, errors, and protocols.

This module provides the foundational types for the harness subsystem:
- Enums: HarnessStatus, RuntimeMode, ToolClass, RecoveryAction, etc.
- Types: HarnessManifest, RuntimeCapabilities, etc.
- Errors: HarnessError hierarchy with structured error data
- Protocols: Interface definitions for DI and testing
- Digests: Content hashing utilities
"""

from __future__ import annotations

from supervisor.harness.core.digests import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    content_digest,
    validate_digest_format,
    verify_digest,
)
from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    HarnessStatus,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
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
    MissingRuntimeCapability,
    PathTraversalAttempt,
    SandboxRequired,
    UnknownHarness,
    UnsupportedProtocol,
)
from supervisor.harness.core.protocols import (
    CompatibilityCheckerProtocol,
    HarnessResolverProtocol,
    ManifestParserProtocol,
)
from supervisor.harness.core.types import (
    ApprovalSpec,
    BudgetSpec,
    GuardSpec,
    HarnessManifest,
    LoopSpec,
    RecoveryPolicy,
    RuntimeCapabilities,
    SubagentsSpec,
    ToolSpec,
)

__all__ = [
    # Digests
    "DIGEST_PATTERN",
    # Enums
    "ApprovalOnTimeout",
    # Types
    "ApprovalSpec",
    "BudgetSpec",
    # Protocols
    "CompatibilityCheckerProtocol",
    "GuardBehavior",
    "GuardSpec",
    # Errors
    "HarnessError",
    "HarnessManifest",
    "HarnessNotOffered",
    "HarnessResolverProtocol",
    "HarnessStatus",
    "IncompatibleHarness",
    "InvalidDigest",
    "InvalidEnvironmentValue",
    "LoopSpec",
    "ManifestError",
    "ManifestParseError",
    "ManifestParserProtocol",
    "ManifestValidationError",
    "MissingRuntimeCapability",
    "PathTraversalAttempt",
    "RecoveryAction",
    "RecoveryPolicy",
    "RuntimeCapabilities",
    "RuntimeMode",
    "SandboxRequired",
    "SubagentsSpec",
    "ToolClass",
    "ToolSpec",
    "UnknownHarness",
    "UnsupportedProtocol",
    "canonical_json_bytes",
    "content_digest",
    "validate_digest_format",
    "verify_digest",
]
