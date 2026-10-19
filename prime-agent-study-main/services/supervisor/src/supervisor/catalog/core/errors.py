"""Catalog exception hierarchy with structured error data.

Design principles:
- Rich context in exception attributes
- No bare Exception raises
- Retryable flag for transient failures
- Structured error codes for API responses
- to_dict() for serialization

Reference: policy/errors.py pattern.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    # Base
    "CatalogError",
    # Integrity
    "DigestMismatch",
    "DigestMissing",
    # Status
    "NotOffered",
    "ImmutableArtifact",
    "FloatingRef",
    # Composition
    "CompositionIncomplete",
    "CompositionDigestRequired",
    "HarnessVersionMismatch",
    # Permission
    "CatalogPermissionDenied",
    "CatalogOwnershipRequired",
    # Storage
    "CatalogStorageError",
    "CatalogConnectionError",
    # Validation
    "InvalidArtifact",
    "InvalidDigest",
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
        """Convert to dictionary for API responses."""
        return {
            "error_code": self.error_code,
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


class ImmutableArtifact(CatalogError):
    """Attempted in-place mutation of an offered artifact body.

    Offered artifacts are immutable. To change, publish a new version
    which will get a new digest.
    """

    error_code = "IMMUTABLE_ARTIFACT"

    def __init__(
        self,
        *,
        kind: str,
        artifact_id: str,
        digest: str,
    ) -> None:
        super().__init__(
            f"cannot mutate offered {kind} {artifact_id!r}@{digest}; "
            "publish a new version instead",
            detail={
                "kind": kind,
                "artifact_id": artifact_id,
                "digest": digest,
            },
        )
        self.kind = kind
        self.artifact_id = artifact_id
        self.digest = digest


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
    """Id-only tip resolve refused when digest is required.

    In production/staging, exact digests are required for reproducibility.
    """

    error_code = "COMPOSITION_DIGEST_REQUIRED"

    def __init__(self, composition_id: str) -> None:
        super().__init__(
            f"composition_digest required for {composition_id!r} "
            "(id-only tip resolve disabled in this environment)",
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


# =============================================================================
# Permission Errors
# =============================================================================

class CatalogPermissionDenied(CatalogError):
    """Principal lacks required grant for catalog operation.

    Check required grants and ensure principal has access.
    """

    error_code = "CATALOG_PERMISSION_DENIED"

    def __init__(
        self,
        *,
        principal_id: str,
        required_grant: str,
        artifact_kind: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        msg = f"permission denied: {principal_id!r} lacks grant {required_grant!r}"
        if artifact_id:
            msg += f" for {artifact_kind}:{artifact_id}"
        super().__init__(
            msg,
            detail={
                "principal_id": principal_id,
                "required_grant": required_grant,
                "artifact_kind": artifact_kind,
                "artifact_id": artifact_id,
            },
        )
        self.principal_id = principal_id
        self.required_grant = required_grant
        self.artifact_kind = artifact_kind
        self.artifact_id = artifact_id


class CatalogOwnershipRequired(CatalogError):
    """Operation requires artifact ownership.

    Only the owner or admin can perform this operation.
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
        super().__init__(
            f"ownership required: {principal_id!r} is not owner of "
            f"{artifact_kind}:{artifact_id} (owner={owner_id!r})",
            detail={
                "principal_id": principal_id,
                "artifact_kind": artifact_kind,
                "artifact_id": artifact_id,
                "owner_id": owner_id,
            },
        )
        self.principal_id = principal_id
        self.artifact_kind = artifact_kind
        self.artifact_id = artifact_id
        self.owner_id = owner_id


# =============================================================================
# Storage Errors
# =============================================================================

class CatalogStorageError(CatalogError):
    """Database or storage operation failed.

    May be transient - consider retry.
    """

    error_code = "CATALOG_STORAGE_ERROR"
    retryable = True

    def __init__(
        self,
        operation: str,
        reason: str,
        *,
        artifact_kind: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        msg = f"storage error during {operation}: {reason}"
        super().__init__(
            msg,
            detail={
                "operation": operation,
                "reason": reason,
                "artifact_kind": artifact_kind,
                "artifact_id": artifact_id,
            },
        )
        self.operation = operation
        self.reason = reason


class CatalogConnectionError(CatalogError):
    """Failed to connect to storage backend.

    Transient error - retry with backoff.
    """

    error_code = "CATALOG_CONNECTION_ERROR"
    retryable = True

    def __init__(self, backend: str, reason: str) -> None:
        super().__init__(
            f"failed to connect to {backend}: {reason}",
            detail={"backend": backend, "reason": reason},
        )
        self.backend = backend
        self.reason = reason


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
