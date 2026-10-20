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
    create_sandbox_provider,
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
)

# Advisory Locks
from supervisor.sandbox.routing.advisory_lock import AdvisoryLockProtocol

# Capabilities
from supervisor.sandbox.routing.capabilities import PROVIDER_CAPABILITIES, SandboxCapability

# Route Stores
from supervisor.sandbox.routing.route_store import MemoryRouteStore

# Router
from supervisor.sandbox.routing.router import SandboxRouter

# Routes
from supervisor.sandbox.routing.routes import RouteStore, SandboxRoute

# Migration
from supervisor.sandbox.routing.sandbox_migration import MigrationResult

# Security - Environment
from supervisor.sandbox.security.env import (
    is_allowed_gateway_inject,
    is_denied_env_key,
    sanitize_sandbox_env,
)

# Security - Guards
from supervisor.sandbox.security.guards import (
    DigestStatus,
    MultiTenantViolation,
    SecurityCheckResult,
    assert_multi_tenant_provider,
    assert_multi_tenant_sandbox_provider,
    is_placeholder_image_digest,
)

__all__ = [
    "PROVIDER_CAPABILITIES",
    "AdvisoryLockProtocol",
    "ByteReader",
    "ByteWriter",
    "DigestStatus",
    "ExecResult",
    "IncompatibleProfile",
    "LocalProcessProvider",
    "LocalProcessSandbox",
    "MemoryRouteStore",
    "MigrationResult",
    "MissingImageDigest",
    "MissingProviderError",
    "MultiTenantViolation",
    "NoneProvider",
    "NoneSandbox",
    "PlaceholderImageError",
    "ProcessHandle",
    "ProviderName",
    "ReapStats",
    "ResolutionError",
    "RouteStore",
    "Sandbox",
    "SandboxCapability",
    "SandboxError",
    "SandboxGone",
    "SandboxInfo",
    "SandboxPathEscape",
    "SandboxProvider",
    "SandboxReattach",
    "SandboxResolution",
    "SandboxRoute",
    "SandboxRouter",
    "SandboxStatus",
    "SandboxTimeout",
    "SecurityCheckResult",
    "SessionLookup",
    "SessionStatus",
    "SnapshotUnsupported",
    "UnknownProviderError",
    "WorkspaceSyncUnsupported",
    "assert_multi_tenant_provider",
    "assert_multi_tenant_sandbox_provider",
    "assert_sandbox_profile_compatible",
    "create_sandbox_provider",
    "is_allowed_gateway_inject",
    "is_denied_env_key",
    "is_placeholder_image_digest",
    "load_pinned_sandbox_profile",
    "reap_orphans",
    "resolve_provider_name",
    "resolve_sandbox_create",
    "sanitize_sandbox_env",
]
