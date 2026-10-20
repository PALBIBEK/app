"""Sandbox isolation layer for secure code execution.

Pluggable backends for running untrusted code, chosen by a session's pinned
sandbox profile (``resolve.py``):
    - NoneProvider: no isolation (harnesses that don't execute anything)
    - LocalProcessProvider: a local process in a confined workspace (development)
    - LocalDockerProvider: Docker containers (production-local)
    - E2BProvider: E2B cloud sandboxes (production-cloud)

    SandboxProvider (ABC)   create() / resume() from snapshot / get() to reattach
          │
    Sandbox (ABC)           spawn() / exec() / read_file() / write_file() / exists()
                            hydrate_workspace() / sync_workspace()
                            pause() / destroy() / is_alive()

Package structure:
    base.py            core abstractions (Sandbox, SandboxProvider)
    factory.py         provider factory
    resolve.py         session pin resolution
    reaper.py          orphan cleanup
    workspace_tree.py  filesystem utilities
    providers/         none, local_process, local_docker, e2b
    security/          environment sanitisation; multi-tenant and image guards

Example:
    >>> from supervisor.sandbox import create_sandbox_provider
    >>> provider = create_sandbox_provider("local_process")
    >>> sandbox = await provider.create(workdir="/workspace")
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
    "ByteReader",
    "ByteWriter",
    "DigestStatus",
    "ExecResult",
    "IncompatibleProfile",
    "LocalProcessProvider",
    "LocalProcessSandbox",
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
    "Sandbox",
    "SandboxError",
    "SandboxGone",
    "SandboxInfo",
    "SandboxPathEscape",
    "SandboxProvider",
    "SandboxReattach",
    "SandboxResolution",
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
