"""Sandbox provider implementations.

Each provider implements the SandboxProvider ABC from sandbox.base:
    - NoneProvider: No isolation (for harnesses with requires_sandbox=false)
    - LocalProcessProvider: Local process with temp directory (development)
    - LocalDockerProvider: Docker containers (production-local)
    - E2BProvider: E2B cloud sandboxes (production-cloud)
"""

from supervisor.sandbox.providers.local_process import (
    LocalProcessProvider,
    LocalProcessSandbox,
)
from supervisor.sandbox.providers.none import NoneProvider, NoneSandbox

__all__ = [
    "LocalProcessProvider",
    "LocalProcessSandbox",
    "NoneProvider",
    "NoneSandbox",
]

# Optional providers (may not be installed)
try:
    from supervisor.sandbox.providers.local_docker import (
        LocalDockerProvider,
        LocalDockerSandbox,
    )
    __all__.extend(["LocalDockerProvider", "LocalDockerSandbox"])
except ImportError:
    pass

try:
    from supervisor.sandbox.providers.e2b import E2BProvider, E2BSandbox
    __all__.extend(["E2BProvider", "E2BSandbox"])
except ImportError:
    pass
