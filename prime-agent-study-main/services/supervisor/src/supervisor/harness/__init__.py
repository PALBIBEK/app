"""Harness subsystem - version resolution, compatibility, and spawn planning.

This module provides harness lifecycle management:

Enums:
    HarnessStatus, RuntimeMode, ToolClass, RecoveryAction - Lifecycle and classification
    GuardBehavior, ApprovalOnTimeout - Configuration options

Types:
    HarnessManifest, SpawnPlan, RuntimeCapabilities - Core dataclasses
    ToolSpec, BudgetSpec, GuardSpec, ApprovalSpec - Configuration specs
    RecoveryPolicy, SubagentsSpec - Policy configurations
    SessionFilesPaths - Materialized file paths

Errors:
    HarnessError and subclasses - Rich exception hierarchy with structured data

Protocols:
    HarnessResolverProtocol, CompatibilityCheckerProtocol, etc. - Interfaces for DI

Services:
    HarnessResolver - Version resolution with thread-safe caching
    HarnessCatalog - In-memory catalog for testing
    CompatibilityChecker - Runtime compatibility validation
    SpawnPlanner - Subprocess spawn plan building

Materializers:
    materialize_harness_binds - Create .yal/ session files
    read_plug_bindings, read_launch_cli - Read configuration files

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
# Enums
# =============================================================================
from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    HarnessStatus,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
    RECOVERY_ACTION_ALIASES,
)

# =============================================================================
# Types
# =============================================================================
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
    default_recovery_policy,
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
    InvalidGatewayConfig,
    ManifestError,
    ManifestParseError,
    ManifestValidationError,
    MissingHarnessCli,
    MissingRuntimeCapability,
    PathTraversalAttempt,
    SandboxRequired,
    SpawnError,
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
    SessionFilesBinderProtocol,
    SpawnPlannerProtocol,
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

# =============================================================================
# Config
# =============================================================================
from supervisor.harness.config import (
    HarnessSettings,
    clear_settings_cache,
    get_settings,
)

# =============================================================================
# Services
# =============================================================================
from supervisor.harness.services import (
    CompatibilityChecker,
    HarnessCatalog,
    HarnessResolver,
    SpawnPlanner,
    build_spawn_plan,
    load_supported_protocol_versions,
)

# =============================================================================
# Materializers
# =============================================================================
from supervisor.harness.materializers import (
    materialize_harness_binds,
    read_launch_cli,
    read_plug_bindings,
    yal_dir,
)

__all__ = [
    # Enums
    "ApprovalOnTimeout",
    "GuardBehavior",
    "HarnessStatus",
    "RecoveryAction",
    "RuntimeMode",
    "ToolClass",
    "RECOVERY_ACTION_ALIASES",
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
    "default_recovery_policy",
    # Errors
    "ApprovalsNotSupported",
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
    "VersionResolutionError",
    # Protocols
    "CompatibilityCheckerProtocol",
    "HarnessResolverProtocol",
    "ManifestParserProtocol",
    "SessionFilesBinderProtocol",
    "SpawnPlannerProtocol",
    # Digests
    "DIGEST_PATTERN",
    "assert_digest",
    "canonical_json_bytes",
    "content_digest",
    "validate_digest_format",
    "verify_digest",
    # Converters - to dict
    "approval_spec_to_dict",
    "budget_spec_to_dict",
    "guard_spec_to_dict",
    "manifest_to_dict",
    "recovery_policy_to_dict",
    "subagents_spec_to_dict",
    "tool_spec_to_dict",
    # Converters - from dict
    "approval_spec_from_dict",
    "budget_spec_from_dict",
    "guard_spec_from_dict",
    "manifest_from_dict",
    "recovery_policy_from_dict",
    "subagents_spec_from_dict",
    "tool_spec_from_dict",
    # Parsing
    "load_manifest",
    "parse_yaml",
    "validate_and_freeze",
    # Validators
    "get_env_url",
    "sanitize_env_value",
    "validate_path_safe",
    "validate_session_dir",
    # Config
    "HarnessSettings",
    "clear_settings_cache",
    "get_settings",
    # Services
    "CompatibilityChecker",
    "HarnessCatalog",
    "HarnessResolver",
    "SpawnPlanner",
    "build_spawn_plan",
    "load_supported_protocol_versions",
    # Materializers
    "materialize_harness_binds",
    "read_launch_cli",
    "read_plug_bindings",
    "yal_dir",
]
