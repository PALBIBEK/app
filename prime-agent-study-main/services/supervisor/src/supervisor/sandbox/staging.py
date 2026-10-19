"""Blob staging for efficient large file transfer.

This module provides blob staging operations that allow sandboxes to efficiently
transfer large files via an external blob store API. The sandbox executes curl
commands internally to download/upload blobs, using capability tokens for auth.

Design (matches QM's createExecBlobStaging):
    1. Control plane mints short-lived capability tokens
    2. Sandbox executes curl to blob store API
    3. Downloads use atomic temp file swap
    4. Uploads verify SHA256 after transfer

Security:
    - Capability tokens expire in 5 minutes (default)
    - SHA256 verification prevents tampering
    - Atomic swaps prevent partial file use

Reference:
    - QM: exec-file-ops.ts createExecBlobStaging
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.sandbox.base import ExecResult

_log = logging.getLogger(__name__)

__all__ = [
    "BLOB_TRANSFER_TIMEOUT_S",
    "BLOB_TRANSFER_TTL_MS",
    "BlobStagingConfig",
    "BlobStagingError",
    "BlobStagingOps",
    "CapabilityTokenMinter",
    "StageOutResult",
    "StagedBlob",
    "create_exec_blob_staging",
]


# =============================================================================
# Constants
# =============================================================================

BLOB_TRANSFER_TTL_MS: Final[int] = 300_000  # 5 minutes
BLOB_TRANSFER_TIMEOUT_S: Final[float] = 120.0  # 2 minutes for transfer
CURL_CONNECT_TIMEOUT_S: Final[int] = 15


# =============================================================================
# Errors
# =============================================================================


class BlobStagingError(Exception):
    """Blob transfer operation failed.

    Attributes:
        message: Error description.
        blob_id: ID of the affected blob, if known.
        direction: Transfer direction ("in" or "out").
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        blob_id: str | None = None,
        direction: str = "unknown",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.blob_id = blob_id
        self.direction = direction
        self.retryable = retryable


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class StagedBlob:
    """Result of staging a blob into the sandbox.

    Attributes:
        blob_id: Unique identifier of the staged blob.
        path: Absolute path where blob was staged in sandbox.
        sha256: SHA256 hash of the blob contents.
        size_bytes: Size of the blob in bytes.
    """

    blob_id: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StageOutResult:
    """Result of staging a file out of the sandbox.

    Attributes:
        blob_id: Unique identifier assigned to the uploaded blob.
        sha256: SHA256 hash verified after upload.
        size_bytes: Size of the uploaded file.
    """

    blob_id: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BlobStagingConfig:
    """Configuration for blob staging operations.

    Attributes:
        api_base_url: Base URL of blob store API (e.g., "https://api.example.com").
        capability_header: HTTP header name for capability token.
        connect_timeout_s: Curl connection timeout in seconds.
    """

    api_base_url: str
    capability_header: str = "X-Capability-Token"
    connect_timeout_s: int = CURL_CONNECT_TIMEOUT_S


# =============================================================================
# Protocols
# =============================================================================


@runtime_checkable
class CapabilityTokenMinter(Protocol):
    """Protocol for minting capability tokens.

    The control plane implements this to issue short-lived tokens
    that authorize blob staging operations.
    """

    async def mint_blob_token(
        self,
        *,
        actor_id: str,
        scope_id: str,
        grant: str,
        ttl_ms: int = BLOB_TRANSFER_TTL_MS,
    ) -> str:
        """Mint a capability token for blob operations.

        Args:
            actor_id: Identity of the requesting actor.
            scope_id: Scope for which the token is valid.
            grant: Access grant type ("read" or "write").
            ttl_ms: Token time-to-live in milliseconds.

        Returns:
            Signed capability token string.
        """
        ...


class BlobStagingOps(ABC):
    """Abstract base for blob staging operations.

    Implementations execute curl commands inside a sandbox to
    transfer files to/from an external blob store.
    """

    __slots__ = ()

    @abstractmethod
    async def stage_in(
        self,
        blob_id: str,
        dest_path: str,
        *,
        timeout: float | None = None,
    ) -> StagedBlob:
        """Download a blob into the sandbox.

        Uses atomic temp file swap to prevent partial downloads.

        Args:
            blob_id: ID of the blob to download.
            dest_path: Absolute destination path in sandbox.
            timeout: Operation timeout in seconds.

        Returns:
            StagedBlob with details of staged file.

        Raises:
            BlobStagingError: If download fails.
        """
        ...

    @abstractmethod
    async def stage_out(
        self,
        source_path: str,
        *,
        timeout: float | None = None,
    ) -> StageOutResult:
        """Upload a file from the sandbox to blob store.

        Computes SHA256 before upload and verifies after.

        Args:
            source_path: Absolute source path in sandbox.
            timeout: Operation timeout in seconds.

        Returns:
            StageOutResult with blob_id and verification hash.

        Raises:
            BlobStagingError: If upload fails.
        """
        ...


# =============================================================================
# Implementation
# =============================================================================


class ExecBlobStaging(BlobStagingOps):
    """Blob staging via exec(curl) commands.

    Matches QM's createExecBlobStaging pattern:
    - Downloads: curl to temp file, then atomic mv
    - Uploads: compute SHA256, curl POST with verification header
    """

    __slots__ = ("_actor_id", "_config", "_exec_fn", "_scope_id", "_token_minter")

    def __init__(
        self,
        *,
        exec_fn: Callable[[list[str], float], Awaitable[ExecResult]],
        config: BlobStagingConfig,
        token_minter: CapabilityTokenMinter,
        actor_id: str,
        scope_id: str,
    ) -> None:
        """Initialize blob staging.

        Args:
            exec_fn: Function to execute commands in sandbox.
                     Signature: (cmd, timeout) -> ExecResult
            config: Blob staging configuration.
            token_minter: Protocol for minting capability tokens.
            actor_id: Actor identity for token minting.
            scope_id: Scope for token authorization.
        """
        self._exec_fn = exec_fn
        self._config = config
        self._token_minter = token_minter
        self._actor_id = actor_id
        self._scope_id = scope_id

    async def stage_in(
        self,
        blob_id: str,
        dest_path: str,
        *,
        timeout: float | None = None,
    ) -> StagedBlob:
        """Download blob via curl with atomic swap."""
        timeout = timeout or BLOB_TRANSFER_TIMEOUT_S

        # Mint read token
        token = await self._token_minter.mint_blob_token(
            actor_id=self._actor_id,
            scope_id=self._scope_id,
            grant="read",
        )

        # Generate temp path with random suffix
        tmp_suffix = uuid.uuid4().hex[:8]
        tmp_path = f"{dest_path}.tmp.{tmp_suffix}"

        # Build curl download command
        url = f"{self._config.api_base_url}/blobs/{blob_id}"
        curl_cmd = (
            f"curl -fsSL "
            f"--connect-timeout {self._config.connect_timeout_s} "
            f'-H "{self._config.capability_header}: {token}" '
            f'-o "{tmp_path}" '
            f'"{url}"'
        )

        # Download to temp file
        script = f'mkdir -p "$(dirname "{dest_path}")" && {curl_cmd}'
        result = await self._exec_fn(["sh", "-c", script], timeout)

        if not result.success:
            raise BlobStagingError(
                f"blob download failed (exit {result.exit_code}): {result.stderr_text[:200]}",
                blob_id=blob_id,
                direction="in",
                retryable=result.exit_code in (6, 7, 28, 56),  # curl retry codes
            )

        # Atomic swap
        mv_result = await self._exec_fn(
            ["mv", "-f", tmp_path, dest_path],
            30.0,
        )
        if not mv_result.success:
            # Clean up temp file
            await self._exec_fn(["rm", "-f", tmp_path], 10.0)
            raise BlobStagingError(
                f"atomic swap failed: {mv_result.stderr_text[:200]}",
                blob_id=blob_id,
                direction="in",
                retryable=False,
            )

        # Get file info
        stat_script = f'sha256sum "{dest_path}" | cut -d" " -f1 && stat -c%s "{dest_path}"'
        stat_result = await self._exec_fn(["sh", "-c", stat_script], 30.0)

        if not stat_result.success:
            raise BlobStagingError(
                f"failed to stat staged file: {stat_result.stderr_text[:200]}",
                blob_id=blob_id,
                direction="in",
                retryable=False,
            )

        lines = stat_result.stdout_text.strip().split("\n")
        sha256 = lines[0] if lines else ""
        size_bytes = int(lines[1]) if len(lines) > 1 else 0

        _log.debug(
            "staged blob %s to %s (%d bytes, sha256=%s...)",
            blob_id,
            dest_path,
            size_bytes,
            sha256[:16],
        )

        return StagedBlob(
            blob_id=blob_id,
            path=dest_path,
            sha256=sha256,
            size_bytes=size_bytes,
        )

    async def stage_out(
        self,
        source_path: str,
        *,
        timeout: float | None = None,
    ) -> StageOutResult:
        """Upload file via curl with SHA256 verification."""
        timeout = timeout or BLOB_TRANSFER_TIMEOUT_S

        # Mint write token
        token = await self._token_minter.mint_blob_token(
            actor_id=self._actor_id,
            scope_id=self._scope_id,
            grant="write",
        )

        # Compute SHA256 first
        sha_result = await self._exec_fn(
            ["sh", "-c", f'sha256sum "{source_path}" | cut -d" " -f1'],
            30.0,
        )
        if not sha_result.success:
            raise BlobStagingError(
                f"failed to compute SHA256: {sha_result.stderr_text[:200]}",
                direction="out",
                retryable=False,
            )
        sha256 = sha_result.stdout_text.strip()

        # Get file size
        stat_result = await self._exec_fn(
            ["stat", "-c%s", source_path],
            10.0,
        )
        if not stat_result.success:
            raise BlobStagingError(
                f"failed to stat source file: {stat_result.stderr_text[:200]}",
                direction="out",
                retryable=False,
            )
        size_bytes = int(stat_result.stdout_text.strip())

        # Build curl upload command
        url = f"{self._config.api_base_url}/blobs"
        curl_cmd = (
            f"curl -fsSL -X POST "
            f"--connect-timeout {self._config.connect_timeout_s} "
            f'-H "{self._config.capability_header}: {token}" '
            f'-H "X-Content-SHA256: {sha256}" '
            f'-T "{source_path}" '
            f'"{url}"'
        )

        result = await self._exec_fn(["sh", "-c", curl_cmd], timeout)

        if not result.success:
            raise BlobStagingError(
                f"blob upload failed (exit {result.exit_code}): {result.stderr_text[:200]}",
                direction="out",
                retryable=result.exit_code in (6, 7, 28, 56),
            )

        # Parse blob_id from response (expected: plain blob ID or JSON)
        response = result.stdout_text.strip()
        if response.startswith("{"):
            # JSON response - extract blob_id
            import json

            try:
                data = json.loads(response)
                blob_id = data.get("blob_id") or data.get("id") or ""
            except json.JSONDecodeError:
                blob_id = response
        else:
            blob_id = response

        if not blob_id:
            raise BlobStagingError(
                "upload succeeded but no blob_id returned",
                direction="out",
                retryable=False,
            )

        _log.debug(
            "staged out %s as blob %s (%d bytes, sha256=%s...)",
            source_path,
            blob_id,
            size_bytes,
            sha256[:16],
        )

        return StageOutResult(
            blob_id=blob_id,
            sha256=sha256,
            size_bytes=size_bytes,
        )


# =============================================================================
# Factory
# =============================================================================


def create_exec_blob_staging(
    *,
    exec_fn: Callable[[list[str], float], Awaitable[ExecResult]],
    config: BlobStagingConfig,
    token_minter: CapabilityTokenMinter,
    actor_id: str,
    scope_id: str,
) -> BlobStagingOps:
    """Create blob staging operations using exec(curl).

    Factory function that matches QM's createExecBlobStaging pattern.

    Args:
        exec_fn: Function to execute commands in sandbox.
                 Signature: (cmd: list[str], timeout: float) -> ExecResult
        config: Blob staging configuration.
        token_minter: Protocol for minting capability tokens.
        actor_id: Actor identity for token minting.
        scope_id: Scope for token authorization.

    Returns:
        BlobStagingOps instance ready for staging operations.

    Example:
        >>> staging = create_exec_blob_staging(
        ...     exec_fn=sandbox.exec,
        ...     config=BlobStagingConfig(api_base_url="https://api.example.com"),
        ...     token_minter=my_token_minter,
        ...     actor_id="agent-123",
        ...     scope_id="workspace-456",
        ... )
        >>> result = await staging.stage_in("blob-789", "/workspace/data.bin")
    """
    return ExecBlobStaging(
        exec_fn=exec_fn,
        config=config,
        token_minter=token_minter,
        actor_id=actor_id,
        scope_id=scope_id,
    )
