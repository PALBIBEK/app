"""Sandbox provider implementations.

Each provider implements the SandboxProvider ABC from sandbox.base:
    - NoneProvider: No isolation (for harnesses with requires_sandbox=false)
    - LocalProcessProvider: Local process with temp directory (development)
    - LocalDockerProvider: Docker containers (production-local)
    - E2BProvider: E2B cloud sandboxes (production-cloud)
"""

from supervisor.sandbox.providers.none import NoneSandbox, NoneProvider
from supervisor.sandbox.providers.local_process import (
    LocalProcessSandbox,
    LocalProcessProvider,
)

__all__ = [
    "NoneProvider",
    "NoneSandbox",
    "LocalProcessProvider",
    "LocalProcessSandbox",
]

# Optional providers (may not be installed)
try:
    from supervisor.sandbox.providers.local_docker import (
        LocalDockerSandbox,
        LocalDockerProvider,
    )
    __all__.extend(["LocalDockerProvider", "LocalDockerSandbox"])
except ImportError:
    pass

try:
    from supervisor.sandbox.providers.e2b import E2BSandbox, E2BProvider
    __all__.extend(["E2BProvider", "E2BSandbox"])
except ImportError:
    pass
