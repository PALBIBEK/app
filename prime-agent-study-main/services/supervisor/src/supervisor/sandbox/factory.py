"""Sandbox provider factory - the single source of truth for provider names.

This module is the ONLY place where provider name strings are mapped to
implementations. All provider instantiation should go through this factory.

Environment Configuration:
    SANDBOX_PROVIDER: Default provider name (none|local_process|local_docker|e2b)

Provider Selection Guide:
    - "none": No isolation, for harnesses with requires_sandbox=false
    - "local_process": Development, temp directory per sandbox
    - "local_docker": Production-local, Docker containers
    - "e2b": Production-cloud, E2B managed sandboxes
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from supervisor.sandbox.base import SandboxProvider

if TYPE_CHECKING:
    from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator


class ProviderName(StrEnum):
    """Canonical provider names."""

    NONE = "none"
    LOCAL_PROCESS = "local_process"
    LOCAL_DOCKER = "local_docker"
    E2B = "e2b"


# Aliases for flexibility
_PROVIDER_ALIASES: dict[str, ProviderName] = {
    "none": ProviderName.NONE,
    "null": ProviderName.NONE,
    "": ProviderName.NONE,
    "local_process": ProviderName.LOCAL_PROCESS,
    "local": ProviderName.LOCAL_PROCESS,
    "process": ProviderName.LOCAL_PROCESS,
    "local_docker": ProviderName.LOCAL_DOCKER,
    "docker": ProviderName.LOCAL_DOCKER,
    "e2b": ProviderName.E2B,
}


class UnknownProviderError(ValueError):
    """Raised when an unknown provider name is requested."""

    def __init__(self, name: str) -> None:
        valid = sorted(set(_PROVIDER_ALIASES.keys()) - {""})
        super().__init__(
            f"unknown sandbox provider {name!r}; "
            f"valid providers: {', '.join(valid)}"
        )
        self.name = name


def resolve_provider_name(name: str | None = None) -> ProviderName:
    """Resolve provider name from argument or environment.

    Args:
        name: Explicit provider name, or None to read from SANDBOX_PROVIDER.

    Returns:
        Canonical ProviderName enum value.

    Raises:
        UnknownProviderError: If name is not recognized.
    """
    raw = (name or os.environ.get("SANDBOX_PROVIDER") or "none").strip().lower()
    canonical = _PROVIDER_ALIASES.get(raw)
    if canonical is None:
        raise UnknownProviderError(raw)
    return canonical


def create_sandbox_provider(
    name: str | None = None,
    *,
    client: Any = None,
    hydrator: WorkspaceHydrator | None = None,
) -> SandboxProvider:
    """Create a sandbox provider by name.

    This is the factory entry point. All sandbox provider instantiation
    should go through this function.

    Args:
        name: Provider name (none|local_process|local_docker|e2b).
            If None, reads from SANDBOX_PROVIDER env var.
        client: Provider-specific client (e.g., Docker client).
            Passed to providers that need it.
        hydrator: WorkspaceHydrator for Track B workspace sync.
            Forwarded to providers that support it (local_process, local_docker).

    Returns:
        SandboxProvider instance ready to create sandboxes.

    Raises:
        UnknownProviderError: If provider name is not recognized.
        ModuleNotFoundError: If provider dependencies are not installed.

    Example:
        # From environment
        provider = create_sandbox_provider()

        # Explicit provider
        provider = create_sandbox_provider("local_docker")

        # With workspace sync
        provider = create_sandbox_provider("local_process", hydrator=hydrator)
    """
    canonical = resolve_provider_name(name)

    if canonical == ProviderName.NONE:
        from supervisor.sandbox.providers.none import NoneProvider
        return NoneProvider()

    if canonical == ProviderName.LOCAL_PROCESS:
        from supervisor.sandbox.providers.local_process import LocalProcessProvider
        return LocalProcessProvider(hydrator=hydrator)

    if canonical == ProviderName.LOCAL_DOCKER:
        from supervisor.sandbox.providers.local_docker import LocalDockerProvider
        return LocalDockerProvider(client=client, hydrator=hydrator)

    if canonical == ProviderName.E2B:
        from supervisor.sandbox.providers.e2b import E2BProvider
        return E2BProvider(client=client)

    # Should be unreachable due to enum exhaustiveness
    raise UnknownProviderError(str(canonical))


