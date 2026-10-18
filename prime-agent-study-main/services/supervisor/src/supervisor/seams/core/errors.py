"""Typed errors for capability seams (fail loud)."""

from __future__ import annotations


class SeamError(Exception):
    """Base for seam / plug failures."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class UnsupportedCapability(SeamError):
    """Request asked for a capability the provider does not advertise."""

    def __init__(self, message: str, *, capability: str = "") -> None:
        super().__init__(message, "UNSUPPORTED_CAPABILITY")
        self.capability = capability


class ProviderMissing(SeamError):
    """Named provider is not registered (or was disposed)."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"subagent provider {name!r} is not registered",
            "PROVIDER_MISSING",
        )
        self.provider_name = name


class ProviderConflict(SeamError):
    """Duplicate registration of the same provider name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"subagent provider {name!r} is already registered",
            "PROVIDER_CONFLICT",
        )
        self.provider_name = name


class BadArgs(SeamError):
    """Invalid request arguments before provider start."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "BAD_ARGS")


class DepthExceeded(SeamError):
    """Delegation depth cap exceeded."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "DEPTH_EXCEEDED")
