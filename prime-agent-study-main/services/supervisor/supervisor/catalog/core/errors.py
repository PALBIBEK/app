"""Catalog errors: each a stable ``error_code``, a message, and the structured ``detail`` behind it.

``to_dict()`` is the API error body (``api.catalog``); ``retryable`` tells the
workflow layer whether a retry could succeed (``temporal.domain``). Nothing in
``detail`` names another tenant: an error is shown to whoever caused it.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CatalogError",
    "CatalogOwnershipRequired",
    "CompositionDigestRequired",
    "CompositionIncompatible",
    "CompositionIncomplete",
    "DigestMismatch",
    "DigestMissing",
    "FloatingRef",
    "HarnessVersionMismatch",
    "InvalidArtifact",
    "InvalidDigest",
    "NotOffered",
]


# =============================================================================
# Base Exception
# =============================================================================

class CatalogError(Exception):
    """Base exception for all catalog errors.

    Attributes:
        error_code: Machine-readable code for API responses
        retryable: Whether the operation may succeed on retry
        detail: Structured context for debugging
    """

    error_code: str = "CATALOG_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        """The API error body: ``code`` and ``message`` (what clients read), plus the detail."""
        return {
            "code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "detail": self.detail,
        }


# =============================================================================
# Integrity Errors
# =============================================================================

class DigestMismatch(CatalogError):
    """Stored artifact bytes do not match the requested digest.

    Indicates content corruption or tampering.
    """

    error_code = "DIGEST_MISMATCH"

    def __init__(self, *, expected: str, actual: str) -> None:
        super().__init__(
            f"digest mismatch: expected={expected!r} actual={actual!r}",
            detail={"expected": expected, "actual": actual},
        )
        self.expected = expected
        self.actual = actual


class DigestMissing(CatalogError):
    """Unknown artifact id/digest in the catalog.

    The requested artifact version does not exist.
    """

    error_code = "DIGEST_MISSING"

    def __init__(
        self,
        *,
        kind: str,
        artifact_id: str,
        digest: str,
    ) -> None:
        super().__init__(
            f"unknown {kind} id={artifact_id!r} digest={digest!r}",
            detail={
                "kind": kind,
                "artifact_id": artifact_id,
                "digest": digest,
            },
        )
        self.kind = kind
        self.artifact_id = artifact_id
        self.digest = digest


class InvalidDigest(CatalogError):
    """Digest format is invalid.

    Expected format: sha256:<64 hex chars>
    """

    error_code = "INVALID_DIGEST"

    def __init__(self, digest: str, reason: str) -> None:
        super().__init__(
            f"invalid digest {digest!r}: {reason}",
            detail={"digest": digest, "reason": reason},
        )
        self.digest = digest
        self.reason = reason


# =============================================================================
# Status Errors
# =============================================================================

class NotOffered(CatalogError):
    """Artifact is absent, or present but not in 'offered' status.

    Cannot use missing, draft or deprecated artifacts for session creation.
    """

    error_code = "NOT_OFFERED"

    def __init__(
        self,
        *,
        kind: str,
        artifact_id: str,
        digest: str,
        status: str = "missing",
    ) -> None:
        super().__init__(
            f"{kind} id={artifact_id!r} digest={digest!r} status={status!r} (not offered)",
            detail={
                "kind": kind,
                "artifact_id": artifact_id,
                "digest": digest,
                "status": status,
            },
        )
        self.kind = kind
        self.artifact_id = artifact_id
        self.digest = digest
        self.status = status


class FloatingRef(CatalogError):
    """Refused floating mutable tag (e.g. 'latest') without digest.

    Production requires exact digests for reproducibility.
    """

    error_code = "FLOATING_REF"

    def __init__(self, ref: str, reason: str | None = None) -> None:
        msg = f"refusing floating ref {ref!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(
            msg,
            detail={"ref": ref, "reason": reason},
        )
        self.ref = ref


# =============================================================================
# Composition Errors
# =============================================================================

class CompositionIncomplete(CatalogError):
    """Required artifact pin absent from composition.

    Compositions must reference a PromptPack and ToolSet at minimum.
    """

    error_code = "COMPOSITION_INCOMPLETE"

    def __init__(
        self,
        composition_id: str,
        missing: str,
    ) -> None:
        super().__init__(
            f"composition {composition_id!r} missing required pin: {missing}",
            detail={
                "composition_id": composition_id,
                "missing": missing,
            },
        )
        self.composition_id = composition_id
        self.missing = missing


class CompositionDigestRequired(CatalogError):
    """A session named a composition without its digest where one is required.

    With ``REQUIRE_COMPOSITION_DIGEST`` set, a session must say exactly which
    version it runs: resolving a name to "whatever is newest" is refused.
    """

    error_code = "COMPOSITION_DIGEST_REQUIRED"

    def __init__(self, composition_id: str) -> None:
        super().__init__(
            f"composition_digest required for {composition_id!r}: "
            "this deployment runs only exact versions (REQUIRE_COMPOSITION_DIGEST)",
            detail={"composition_id": composition_id},
        )
        self.composition_id = composition_id


class HarnessVersionMismatch(CatalogError):
    """Request harness_version does not match composition pin.

    Session must be created with compatible harness version.
    """

    error_code = "HARNESS_VERSION_MISMATCH"

    def __init__(self, *, requested: str, composition: str) -> None:
        super().__init__(
            f"harness_version mismatch: requested={requested!r} composition={composition!r}",
            detail={
                "requested": requested,
                "composition": composition,
            },
        )
        self.requested = requested
        self.composition = composition


class CompositionIncompatible(CatalogError):
    """A composition's parts cannot work together as pinned (e.g. a sticky harness
    on a sandbox profile that may not be kept between turns)."""

    error_code = "COMPOSITION_INCOMPATIBLE"

    def __init__(self, composition_id: str, reason: str) -> None:
        super().__init__(
            f"composition {composition_id!r} cannot run: {reason}",
            detail={"composition_id": composition_id, "reason": reason},
        )
        self.composition_id = composition_id
        self.reason = reason


# =============================================================================
# Permission Errors
# =============================================================================

class CatalogOwnershipRequired(CatalogError):
    """The name belongs to someone else: shipped (``system``) or another tenant's.

    Another tenant is never named, in the message or the detail: the caller learns
    the name is taken, not by whom.
    """

    error_code = "CATALOG_OWNERSHIP_REQUIRED"

    def __init__(
        self,
        *,
        principal_id: str,
        artifact_kind: str,
        artifact_id: str,
        owner_id: str | None,
    ) -> None:
        shipped = owner_id == "system"
        super().__init__(
            f"{artifact_kind} {artifact_id!r} "
            + ("is shipped and read-only" if shipped else "belongs to another owner")
            + "; publish it under a new name",
            detail={
                "artifact_kind": artifact_kind,
                "artifact_id": artifact_id,
                **({"owner_id": owner_id} if shipped else {}),
            },
        )
        self.principal_id = principal_id
        self.artifact_kind = artifact_kind
        self.artifact_id = artifact_id
        self.owner_id = owner_id


# =============================================================================
# Validation Errors
# =============================================================================

class InvalidArtifact(CatalogError):
    """Artifact data is invalid or malformed.

    Check artifact structure and required fields.
    """

    error_code = "INVALID_ARTIFACT"

    def __init__(
        self,
        kind: str,
        artifact_id: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"invalid {kind} {artifact_id!r}: {reason}",
            detail={
                "kind": kind,
                "artifact_id": artifact_id,
                "reason": reason,
            },
        )
        self.kind = kind
        self.artifact_id = artifact_id
        self.reason = reason
