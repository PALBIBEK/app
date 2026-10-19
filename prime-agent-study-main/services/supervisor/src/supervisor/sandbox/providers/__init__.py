"""Sandbox provider implementations.

Each provider implements the SandboxProvider ABC from sandbox.base:
    - NoneProvider: No isolation (for harnesses with requires_sandbox=false)
    - LocalProcessProvider: Local process with temp directory (development)
    - LocalDockerProvider: Docker containers (production-local)
    - E2BProvider: E2B cloud sandboxes (production-cloud)
"""

# Docker's and e2b's SDKs are imported when a provider is built, so these
# modules import everywhere.
from supervisor.sandbox.providers.e2b import E2BProvider, E2BSandbox
from supervisor.sandbox.providers.local_docker import LocalDockerProvider, LocalDockerSandbox
from supervisor.sandbox.providers.local_process import (
    LocalProcessProvider,
    LocalProcessSandbox,
)
from supervisor.sandbox.providers.none import NoneProvider, NoneSandbox

__all__ = [
    "E2BProvider",
    "E2BSandbox",
    "LocalDockerProvider",
    "LocalDockerSandbox",
    "LocalProcessProvider",
    "LocalProcessSandbox",
    "NoneProvider",
    "NoneSandbox",
]
