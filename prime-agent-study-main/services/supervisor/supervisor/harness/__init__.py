"""Harness subsystem - the versioned agent manifests sessions pin, their resolution and compatibility.

This module provides harness lifecycle management:

Enums:
    HarnessStatus, RuntimeMode, ToolClass, RecoveryAction - Lifecycle and classification
    GuardBehavior, ApprovalOnTimeout - Configuration options

Types:
    HarnessManifest, RuntimeCapabilities - Core dataclasses
    ToolSpec, BudgetSpec, GuardSpec, ApprovalSpec - Configuration specs
    RecoveryPolicy, SubagentsSpec - Policy configurations

Errors:
    HarnessError and subclasses - Rich exception hierarchy with structured data

Protocols:
    HarnessResolverProtocol, CompatibilityCheckerProtocol, etc. - Interfaces for DI

Services:
    HarnessResolver - Version resolution with thread-safe caching
    HarnessCatalog - In-memory catalog for testing
    CompatibilityChecker - Runtime compatibility validation

Config:
    HarnessSettings - Environment-based configuration
    get_settings - Cached settings access

Example:
    from supervisor.harness import (
        HarnessResolver,
        HarnessCatalog,
        HarnessManifest,
        UnknownHarness,
        CompatibilityChecker,
    )

    # Setup
    catalog = HarnessCatalog()
    resolver = HarnessResolver(catalog=catalog)

    # Resolve version
    manifest = await resolver.resolve("v1.2.3", for_create=True)

    # Check compatibility
    checker = CompatibilityChecker()
    checker.check(manifest, sandbox_available=True)
"""

from __future__ import annotations

# =============================================================================
# Config
# =============================================================================
from supervisor.harness.config import (
    HarnessSettings,
    clear_settings_cache,
    get_settings,
)

# =============================================================================
# Digests
# =============================================================================
from supervisor.harness.core.digests import (
    DIGEST_PATTERN,
    assert_digest,
    canonical_json_bytes,
    content_digest,
    validate_digest_format,
    verify_digest,
)

# =============================================================================
# Enums
# =============================================================================
from supervisor.harness.core.enums import (
    RECOVERY_ACTION_ALIASES,
    ApprovalOnTimeout,
    GuardBehavior,
    HarnessStatus,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
)

# =============================================================================
# Errors
# =============================================================================
from supervisor.harness.core.errors import (
    ApprovalsNotSupported,
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
    VersionResolutionError,
)

# =============================================================================
# Protocols
# =============================================================================
from supervisor.harness.core.protocols import (
    CompatibilityCheckerProtocol,
    HarnessResolverProtocol,
    ManifestParserProtocol,
)

# =============================================================================
# Types
# =============================================================================
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
    default_recovery_policy,
)

# =============================================================================
# Materializers
# =============================================================================
# =============================================================================
# Services
# =============================================================================
from supervisor.harness.services import (
    CompatibilityChecker,
    HarnessCatalog,
    HarnessResolver,
    load_supported_protocol_versions,
)

# =============================================================================
# Storage - Converters
# =============================================================================
from supervisor.harness.storage.converters import (
    approval_spec_from_dict,
    approval_spec_to_dict,
    budget_spec_from_dict,
    budget_spec_to_dict,
    guard_spec_from_dict,
    guard_spec_to_dict,
    load_manifest,
    manifest_from_dict,
    manifest_to_dict,
    parse_yaml,
    recovery_policy_from_dict,
    recovery_policy_to_dict,
    subagents_spec_from_dict,
    subagents_spec_to_dict,
    tool_spec_from_dict,
    tool_spec_to_dict,
    validate_and_freeze,
)

# =============================================================================
# Storage - Validators
# =============================================================================
from supervisor.harness.storage.validators import (
    get_env_url,
    sanitize_env_value,
    validate_path_safe,
    validate_session_dir,
)

__all__ = [
    "DIGEST_PATTERN",
    "RECOVERY_ACTION_ALIASES",
    "ApprovalOnTimeout",
    "ApprovalSpec",
    "ApprovalsNotSupported",
    "BudgetSpec",
    "CompatibilityChecker",
    "CompatibilityCheckerProtocol",
    "GuardBehavior",
    "GuardSpec",
    "HarnessCatalog",
    "HarnessError",
    "HarnessManifest",
    "HarnessNotOffered",
    "HarnessResolver",
    "HarnessResolverProtocol",
    "HarnessSettings",
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
    "VersionResolutionError",
    "approval_spec_from_dict",
    "approval_spec_to_dict",
    "assert_digest",
    "budget_spec_from_dict",
    "budget_spec_to_dict",
    "canonical_json_bytes",
    "clear_settings_cache",
    "content_digest",
    "default_recovery_policy",
    "get_env_url",
    "get_settings",
    "guard_spec_from_dict",
    "guard_spec_to_dict",
    "load_manifest",
    "load_supported_protocol_versions",
    "manifest_from_dict",
    "manifest_to_dict",
    "parse_yaml",
    "recovery_policy_from_dict",
    "recovery_policy_to_dict",
    "sanitize_env_value",
    "subagents_spec_from_dict",
    "subagents_spec_to_dict",
    "tool_spec_from_dict",
    "tool_spec_to_dict",
    "validate_and_freeze",
    "validate_digest_format",
    "validate_path_safe",
    "validate_session_dir",
    "verify_digest",
]
