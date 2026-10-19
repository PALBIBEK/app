"""Sandbox resolution — maps session pins to provider and image configuration.

This module bridges the gap between high-level session configuration
(SandboxProfile, HarnessManifest) and low-level sandbox creation.

Resolution Flow:
    1. Load sandbox profile from session pins (if pinned)
    2. Validate profile compatibility with harness manifest
    3. Resolve provider (profile override or deploy default)
    4. Resolve image digest (profile pin or manifest default)
    5. Validate image digest against placeholder guards

Key Concepts:
    - SandboxProfile: User-selectable sandbox configuration
    - HarnessManifest: Harness requirements (requires_sandbox, image_digest)
    - SessionPins: Session-specific configuration selections

Reference: D14 (placeholder rejection), D10 (provider resolution)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from supervisor.sandbox.base import SandboxProvider
from supervisor.sandbox.factory import create_sandbox_provider
from supervisor.sandbox.security.guards import (
    assert_multi_tenant_provider,
    is_placeholder_image_digest,
    multi_tenant_strict_enabled,
)

if TYPE_CHECKING:
    from supervisor.catalog.storage import CompositionCatalog
    from supervisor.catalog import SandboxProfile, SessionPins
    from supervisor.harness import HarnessManifest
    from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator

__all__ = [
    "load_pinned_sandbox_profile",
    "assert_sandbox_profile_compatible",
    "resolve_sandbox_create",
    "SandboxResolution",
    "ResolutionError",
    "IncompatibleProfile",
    "MissingImageDigest",
    "PlaceholderImageError",
]


# =============================================================================
# Errors
# =============================================================================

class ResolutionError(Exception):
    """Base error for sandbox resolution failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class IncompatibleProfile(ResolutionError):
    """Raised when sandbox profile doesn't match harness requirements."""

    def __init__(
        self,
        harness_version: str,
        profile_name: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"harness {harness_version!r} incompatible with "
            f"sandbox_profile {profile_name!r}: {reason}"
        )
        self.harness_version = harness_version
        self.profile_name = profile_name
        self.reason = reason


class MissingImageDigest(ResolutionError):
    """Raised when sandbox requires image but none is configured."""

    def __init__(self) -> None:
        super().__init__(
            "sandbox required but no image_digest on manifest or profile"
        )


class PlaceholderImageError(ResolutionError):
    """Raised when image digest is a placeholder/fixture value."""

    def __init__(self, digest: str) -> None:
        super().__init__(
            f"sandbox image_digest is a placeholder fixture ({digest!r}); "
            "build harness/images/Dockerfile and pin the real image id (D14)"
        )
        self.digest = digest


class MissingProviderError(ResolutionError):
    """Raised when sandbox is required but no provider is configured."""

    def __init__(self) -> None:
        super().__init__("sandbox required but no provider configured")


# =============================================================================
# Resolution Result
# =============================================================================

@dataclass(frozen=True, slots=True)
class SandboxResolution:
    """Result of sandbox resolution.

    Attributes:
        provider: The resolved sandbox provider (None if not required).
        image_digest: The resolved image digest (None if not required).
        requires_sandbox: Whether the harness requires a sandbox.
        profile_used: Name of the sandbox profile used, if any.
    """

    provider: SandboxProvider | None
    image_digest: str | None
    requires_sandbox: bool
    profile_used: str | None = None

    @property
    def has_sandbox(self) -> bool:
        """True if resolution includes a sandbox."""
        return self.provider is not None and self.image_digest is not None


# =============================================================================
# Profile Loading
# =============================================================================

def load_pinned_sandbox_profile(
    pins: SessionPins,
    catalog: CompositionCatalog,
) -> SandboxProfile | None:
    """Load sandbox profile from session pins.

    Retrieves the SandboxProfile artifact from the catalog using the
    ID and digest specified in session pins.

    Args:
        pins: Session pins containing profile ID and digest.
        catalog: Composition catalog to load from.

    Returns:
        SandboxProfile if pinned, None if not specified.

    Raises:
        KeyError: If pinned profile not found in catalog.
        AssertionError: If artifact type is wrong.

    Example:
        >>> profile = load_pinned_sandbox_profile(pins, catalog)
        >>> if profile:
        ...     print(f"Using profile: {profile.name}")
    """
    if not pins.sandbox_profile_id or not pins.sandbox_profile_digest:
        return None

    artifact = catalog.get(
        "sandbox_profile",
        pins.sandbox_profile_id,
        pins.sandbox_profile_digest,
        require_offered=True,
    )

    # Type guard for catalog consistency
    from supervisor.catalog import SandboxProfile
    assert isinstance(artifact, SandboxProfile), (
        f"expected SandboxProfile, got {type(artifact).__name__}"
    )

    return artifact


# =============================================================================
# Compatibility Validation
# =============================================================================

def assert_sandbox_profile_compatible(
    manifest: HarnessManifest,
    profile: SandboxProfile,
) -> None:
    """Validate sandbox profile against harness requirements.

    Ensures the profile's provider matches what the harness expects:
        - Harness requires_sandbox=True → profile must have real provider
        - Harness requires_sandbox=False → profile must use "none" provider

    Args:
        manifest: Harness manifest with requirements.
        profile: Sandbox profile to validate.

    Raises:
        IncompatibleProfile: If profile doesn't match harness requirements.

    Example:
        >>> assert_sandbox_profile_compatible(manifest, profile)
        # Raises IncompatibleProfile if incompatible
    """
    profile_has_sandbox = profile.provider not in ("none", "")

    if manifest.requires_sandbox and not profile_has_sandbox:
        raise IncompatibleProfile(
            manifest.harness_version,
            profile.name,
            f"requires_sandbox but profile uses provider={profile.provider!r}",
        )

    if not manifest.requires_sandbox and profile_has_sandbox:
        raise IncompatibleProfile(
            manifest.harness_version,
            profile.name,
            f"does not require sandbox but profile uses provider={profile.provider!r}",
        )


# =============================================================================
# Provider Resolution
# =============================================================================

def _resolve_provider(
    profile_provider: str,
    *,
    deploy_provider: SandboxProvider | None,
    client: object | None,
    hydrator: WorkspaceHydrator | None = None,
    strict: bool = False,
) -> SandboxProvider:
    """Instantiate provider from profile, with fallback to deploy provider.

    Resolution strategy:
        1. Try to create provider from profile specification
        2. If optional dependencies missing, fall back to deploy_provider
        3. If no fallback available, propagate the import error

    This enables graceful degradation when optional provider backends
    (like docker-py) aren't installed but a test provider is injected.

    Args:
        profile_provider: Provider name from sandbox profile.
        deploy_provider: Fallback provider from deployment config.
        client: Provider-specific client (e.g., Docker client).

    Returns:
        Resolved SandboxProvider instance.

    Raises:
        ModuleNotFoundError: If provider deps missing and no fallback.
    """
    try:
        # The deploy provider's hydrator, so profile sandboxes can hydrate and sync scopes.
        return create_sandbox_provider(profile_provider, client=client, hydrator=hydrator)
    except ModuleNotFoundError:
        # Only a stand-in of the same kind (a test double) may replace it: a different
        # provider would silently change the isolation the profile asked for.
        if deploy_provider is not None and not strict and _same_kind(deploy_provider, profile_provider):
            return deploy_provider
        raise


# =============================================================================
# Main Resolution Function
# =============================================================================

def resolve_sandbox_create(
    manifest: HarnessManifest,
    *,
    deploy_provider: SandboxProvider | None,
    profile: SandboxProfile | None = None,
    client: object | None = None,
    hydrator: WorkspaceHydrator | None = None,
    multi_tenant_strict: bool | None = None,
) -> tuple[SandboxProvider | None, str | None]:
    """Resolve sandbox provider and image for sandbox creation.

    Main entry point for sandbox resolution. Combines manifest requirements,
    profile overrides, and deployment defaults to produce creation parameters.

    Resolution Logic:
        1. If harness doesn't require sandbox → return (None, None)
        2. Validate profile compatibility if provided
        3. Resolve provider from profile or deploy default
        4. Resolve image from profile or manifest
        5. Validate image is not a placeholder

    Args:
        manifest: Harness manifest with requirements.
        deploy_provider: Default provider from deployment config.
        profile: Optional sandbox profile override.
        client: Provider-specific client.

    Returns:
        Tuple of (provider, image_digest) for SandboxProvider.create().

    Raises:
        IncompatibleProfile: If profile doesn't match manifest.
        MissingProviderError: If sandbox required but no provider.
        MissingImageDigest: If sandbox required but no image.
        PlaceholderImageError: If image is a fixture/placeholder.

    Example:
        >>> provider, image = resolve_sandbox_create(
        ...     manifest,
        ...     deploy_provider=default_provider,
        ...     profile=user_profile,
        ... )
        >>> if provider:
        ...     sandbox = await provider.create(image=image)
    """
    # No sandbox required - short circuit
    if not manifest.requires_sandbox:
        if profile is not None:
            assert_sandbox_profile_compatible(manifest, profile)
        return None, None

    # Sandbox required - resolve provider and image
    strict = multi_tenant_strict_enabled() if multi_tenant_strict is None else multi_tenant_strict
    if profile is not None:
        assert_sandbox_profile_compatible(manifest, profile)
        # A tenant-published profile picks its provider: strict mode must hold per session.
        assert_multi_tenant_provider(_provider_name(profile.provider), multi_tenant_strict=strict)
        provider = _resolve_provider(
            profile.provider,
            deploy_provider=deploy_provider,
            client=client,
            hydrator=hydrator,
            strict=strict,
        )
        # Profile image takes precedence, fall back to manifest
        image = profile.image_digest or manifest.image_digest
    else:
        provider = deploy_provider
        image = manifest.image_digest

    # Validate we have what we need
    if provider is None:
        raise MissingProviderError()
    assert_multi_tenant_provider(
        _provider_name(getattr(provider, "name", "")), multi_tenant_strict=strict
    )

    # D14: an image a provider will actually run must be pinned to a real digest. A
    # host-process sandbox runs no image, so the shipped placeholder does not block it.
    if _runs_images(provider):
        if not image:
            raise MissingImageDigest()
        if is_placeholder_image_digest(image):
            raise PlaceholderImageError(image)

    return provider, image


def resolve_sandbox_create_full(
    manifest: HarnessManifest,
    *,
    deploy_provider: SandboxProvider | None,
    profile: SandboxProfile | None = None,
    client: object | None = None,
) -> SandboxResolution:
    """Resolve sandbox with full metadata (alternative API).

    Same as resolve_sandbox_create but returns a SandboxResolution
    dataclass with additional metadata.

    Args:
        manifest: Harness manifest with requirements.
        deploy_provider: Default provider from deployment config.
        profile: Optional sandbox profile override.
        client: Provider-specific client.

    Returns:
        SandboxResolution with provider, image, and metadata.

    Example:
        >>> resolution = resolve_sandbox_create_full(manifest, ...)
        >>> if resolution.has_sandbox:
        ...     print(f"Using profile: {resolution.profile_used}")
    """
    provider, image = resolve_sandbox_create(
        manifest,
        deploy_provider=deploy_provider,
        profile=profile,
        client=client,
    )

    return SandboxResolution(
        provider=provider,
        image_digest=image,
        requires_sandbox=manifest.requires_sandbox,
        profile_used=profile.name if profile else None,
    )


def _provider_name(provider: object) -> str:
    """A provider's name as the strict-mode guard spells it (enum or string)."""
    return str(getattr(provider, "value", provider))


def _same_kind(provider: object, requested: object) -> bool:
    return _provider_name(getattr(provider, "name", "")) == _provider_name(requested)


_IMAGELESS_PROVIDERS = frozenset({"local_process", "none"})


def _runs_images(provider: object) -> bool:
    return _provider_name(getattr(provider, "name", "")) not in _IMAGELESS_PROVIDERS
