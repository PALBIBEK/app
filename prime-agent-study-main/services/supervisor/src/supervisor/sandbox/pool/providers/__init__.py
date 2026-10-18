"""
Sandbox provider implementations.

Available providers:
- DockerSandboxProvider: Docker container-based isolation
- E2BSandboxProvider: E2B cloud sandbox service
- LocalProcessProvider: Local subprocess (development only)

Each provider implements the SandboxProvider protocol and can be
used interchangeably with the SandboxPool.

Provider Selection:
    - Development: LocalProcessProvider (no isolation, fast)
    - Testing: DockerSandboxProvider (container isolation)
    - Production: E2BSandboxProvider or DockerSandboxProvider

Example:
    ```python
    from supervisor.sandbox.pool.providers import DockerSandboxProvider

    provider = DockerSandboxProvider(DockerConfig(
        image="python:3.11-slim",
        memory_limit="512m",
    ))

    pool = SandboxPool(provider=provider, config=pool_config)
    ```
"""

from supervisor.sandbox.pool.providers.docker import (
    DockerSandboxProvider,
    DockerConfig,
)
from supervisor.sandbox.pool.providers.local import (
    LocalProcessProvider,
    LocalConfig,
)

__all__ = [
    "DockerSandboxProvider",
    "DockerConfig",
    "LocalProcessProvider",
    "LocalConfig",
]
