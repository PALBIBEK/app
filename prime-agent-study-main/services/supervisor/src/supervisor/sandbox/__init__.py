"""Sandbox isolation layer for secure code execution.

This package provides pluggable sandbox backends for running untrusted code:
    - NoneProvider: No isolation (for harnesses that don't need execution)
    - LocalProcessProvider: Local process with workspace (development)
    - LocalDockerProvider: Docker containers (production-local)
    - E2BProvider: E2B cloud sandboxes (production-cloud)

Architecture:

    ┌─────────────────────────────────────────────────────────────┐
    │                    SandboxRouter                             │
    │   create(scope_id) → Sandbox (routed to backend)            │
    │   pick(scope_id) → (backend_name, provider)                 │
    │   route_for(scope_id) → SandboxRoute | None                 │
    └─────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ┌──────────────┐    ┌──────────────┐
            │ RouteStore   │    │ Security     │
            │ (DB routes)  │    │ Guards       │
            └──────────────┘    └──────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │                  SandboxProvider (ABC)                       │
    │   create() → Sandbox                                         │
    │   resume() → Sandbox (from snapshot)                        │
    │   get() → Sandbox | None (reattach)                         │
    └─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌──────────┐        ┌──────────┐        ┌──────────┐
    │   None   │        │  Local   │        │   E2B    │
    │ Provider │        │ Process  │        │ Provider │
    └──────────┘        └──────────┘        └──────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                    Sandbox (ABC)                             │
    │   spawn() → ProcessHandle (long-lived)                      │
    │   exec() → ExecResult (short command)                       │
    │   read_file() / write_file() / exists()                     │
    │   hydrate_workspace() / sync_workspace() (Track B)          │
    │   pause() / destroy() / is_alive()                          │
    └─────────────────────────────────────────────────────────────┘

Package Structure:
    sandbox/
    ├── base.py              # Core abstractions (Sandbox, SandboxProvider)
    ├── factory.py           # Provider/router factories
    ├── resolve.py           # Session pin resolution
    ├── reaper.py            # Orphan cleanup
    ├── workspace_tree.py    # Filesystem utilities
    │
    ├── providers/           # Provider implementations
    │   ├── none.py
    │   ├── local_process.py
    │   ├── local_docker.py
    │   └── e2b.py
    │
    ├── security/            # Security layer
    │   ├── env.py           # Environment sanitization
    │   └── guards.py        # Multi-tenant + image validation
    │
    ├── routing/             # Dynamic routing system (QM pattern)
    │   ├── routes.py        # SandboxRoute + RouteStore protocol
    │   ├── route_store.py   # Memory/Postgres storage (15s cache)
    │   ├── router.py        # SandboxRouter with guards
    │   ├── capabilities.py  # Capability detection
    │   ├── advisory_lock.py # Postgres advisory locks
    │   └── sandbox_migration.py  # Backend migration
    │
    └── pool/                # Pool management
        ├── manager.py
        └── provider.py

Example (Direct Provider):
    >>> from supervisor.sandbox import create_sandbox_provider
    >>>
    >>> provider = create_sandbox_provider("local_process")
    >>> sandbox = await provider.create(workdir="/workspace")
    >>> result = await sandbox.exec(["python", "-c", "print('hello')"])
    >>> await sandbox.destroy()

Example (Router - Recommended):
    >>> from supervisor.sandbox import create_router, MemoryRouteStore
    >>>
    >>> router = create_router()
    >>> sandbox = await router.create("scope-123")  # Routes to default backend
    >>> result = await sandbox.exec(["python", "-c", "print('hello')"])
    >>> await sandbox.destroy()
"""

from __future__ import annotations

# Core abstractions
from supervisor.sandbox.base import (
    ByteReader,
    ByteWriter,
    ExecResult,
    ProcessHandle,
    ProvisionError,
    Sandbox,
    SandboxError,
    SandboxGone,
    SandboxInfo,
    SandboxPathEscape,
    SandboxProvider,
    SandboxStatus,
    SandboxTimeout,
    SnapshotUnsupported,
    WorkspaceSyncUnsupported,
)

# Factory
# Factory additions
from supervisor.sandbox.factory import (
    ProviderName,
    UnknownProviderError,
    create_migration_runner,
    create_router,
    create_sandbox_provider,
    discover_backends,
    get_default_provider_name,
    is_isolation_provider,
    resolve_provider_name,
)
from supervisor.sandbox.providers.local_process import (
    LocalProcessProvider,
    LocalProcessSandbox,
)

# Providers
from supervisor.sandbox.providers.none import NoneProvider, NoneSandbox

# Lifecycle
from supervisor.sandbox.reaper import (
    ReapStats,
    SandboxReattach,
    SessionLookup,
    SessionStatus,
    reap_orphans,
)

# Resolution
from supervisor.sandbox.resolve import (
    IncompatibleProfile,
    MissingImageDigest,
    MissingProviderError,
    PlaceholderImageError,
    ResolutionError,
    SandboxResolution,
    assert_sandbox_profile_compatible,
    load_pinned_sandbox_profile,
    resolve_sandbox_create,
    resolve_sandbox_create_full,
)

# Advisory Locks
from supervisor.sandbox.routing.advisory_lock import (
    AdvisoryLock,
    AdvisoryLockProtocol,
    MemoryAdvisoryLock,
)

# Capabilities
from supervisor.sandbox.routing.capabilities import (
    PROVIDER_CAPABILITIES,
    SandboxCapability,
    aggregate_capabilities,
    capabilities_lost_for_backends,
    capabilities_lost_moving_to,
    detect_capabilities,
    detect_provider_capabilities,
)

# Route Stores
from supervisor.sandbox.routing.route_store import (
    MemoryRouteStore,
    PostgresRouteStore,
)

# Router
from supervisor.sandbox.routing.router import (
    BackendUnavailableError,
    CapabilityUnsupportedError,
    RouterError,
    SandboxRouter,
    SandboxRouterImpl,
    create_sandbox_router,
)

# Routes
from supervisor.sandbox.routing.routes import (
    RouteNotFoundError,
    RouteStore,
    SandboxRoute,
)

# Migration
from supervisor.sandbox.routing.sandbox_migration import (
    MigrationBlockedError,
    MigrationResult,
    SandboxMigrationRunner,
)

# Security - Environment
from supervisor.sandbox.security.env import (
    EnvSanitizer,
    is_allowed_gateway_inject,
    is_denied_env_key,
    sanitize_sandbox_env,
)

# Security - Guards
from supervisor.sandbox.security.guards import (
    DigestStatus,
    ImageDigestError,
    MultiTenantViolation,
    SecurityCheckResult,
    assert_multi_tenant_provider,
    assert_multi_tenant_sandbox_provider,
    is_placeholder_image_digest,
    validate_image_digest,
)

__all__ = [
    # Core abstractions
    "Sandbox",
    "SandboxProvider",
    "SandboxInfo",
    "SandboxStatus",
    "SandboxError",
    "SandboxGone",
    "SandboxTimeout",
    "SandboxPathEscape",
    "SnapshotUnsupported",
    "WorkspaceSyncUnsupported",
    "ProvisionError",
    "ProcessHandle",
    "ByteReader",
    "ByteWriter",
    "ExecResult",
    # Factory
    "create_sandbox_provider",
    "resolve_provider_name",
    "get_default_provider_name",
    "is_isolation_provider",
    "ProviderName",
    "UnknownProviderError",
    # Providers
    "NoneProvider",
    "NoneSandbox",
    "LocalProcessProvider",
    "LocalProcessSandbox",
    # Security - Environment
    "sanitize_sandbox_env",
    "is_denied_env_key",
    "is_allowed_gateway_inject",
    "EnvSanitizer",
    # Security - Guards
    "assert_multi_tenant_provider",
    "assert_multi_tenant_sandbox_provider",
    "is_placeholder_image_digest",
    "validate_image_digest",
    "DigestStatus",
    "SecurityCheckResult",
    "MultiTenantViolation",
    "ImageDigestError",
    # Resolution
    "load_pinned_sandbox_profile",
    "assert_sandbox_profile_compatible",
    "resolve_sandbox_create",
    "resolve_sandbox_create_full",
    "SandboxResolution",
    "ResolutionError",
    "IncompatibleProfile",
    "MissingImageDigest",
    "MissingProviderError",
    "PlaceholderImageError",
    # Lifecycle
    "reap_orphans",
    "ReapStats",
    "SessionStatus",
    "SessionLookup",
    "SandboxReattach",
    # Routes
    "SandboxRoute",
    "RouteStore",
    "RouteNotFoundError",
    # Route Stores
    "MemoryRouteStore",
    "PostgresRouteStore",
    # Advisory Locks
    "AdvisoryLock",
    "AdvisoryLockProtocol",
    "MemoryAdvisoryLock",
    # Capabilities
    "SandboxCapability",
    "PROVIDER_CAPABILITIES",
    "detect_capabilities",
    "detect_provider_capabilities",
    "capabilities_lost_moving_to",
    "capabilities_lost_for_backends",
    "aggregate_capabilities",
    # Router
    "SandboxRouter",
    "SandboxRouterImpl",
    "create_sandbox_router",
    "RouterError",
    "BackendUnavailableError",
    "CapabilityUnsupportedError",
    # Migration
    "SandboxMigrationRunner",
    "MigrationResult",
    "MigrationBlockedError",
    # Factory additions
    "create_router",
    "create_migration_runner",
    "discover_backends",
]
