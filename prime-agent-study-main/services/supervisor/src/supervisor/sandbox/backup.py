"""Computer backup for workspace and home directories.

This module provides backup operations that create tar archives of sandbox
directories with intelligent credential exclusion. Uses `find -print0 | tar`
for safe filename handling.

Design (matches QM's createExecBackup):
    1. Dual-area support: workspace and home
    2. Credential exclusion patterns (mandatory by default)
    3. SHA256 fingerprinting of archives
    4. Temp file in /tmp with automatic cleanup

Security:
    - Credential files are excluded by default (.ssh, .aws, etc.)
    - Virtual environments excluded to prevent path leakage
    - Cache directories excluded to reduce archive size

Reference:
    - QM: exec-file-ops.ts createExecBackup
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Awaitable, Callable, Final

if TYPE_CHECKING:
    from supervisor.sandbox.base import ExecResult

_log = logging.getLogger(__name__)

__all__ = [
    "BackupArea",
    "BackupResult",
    "BackupError",
    "BackupOps",
    "create_exec_backup",
    "CREDENTIAL_EXCLUSIONS",
    "CACHE_EXCLUSIONS",
    "BACKUP_TIMEOUT_S",
]


# =============================================================================
# Constants
# =============================================================================

BACKUP_TIMEOUT_S: Final[float] = 180.0  # 3 minutes for backup

# Credential files/directories to exclude from backups
CREDENTIAL_EXCLUSIONS: Final[tuple[str, ...]] = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".docker/config.json",
    "*credentials*",
    "*secret*",
    "*token*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
)

# Cache directories to exclude (reduce archive size)
CACHE_EXCLUSIONS: Final[tuple[str, ...]] = (
    ".cache",
    "__pycache__",
    "node_modules",
    ".npm",
    ".pip",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)


# =============================================================================
# Enums
# =============================================================================


class BackupArea(StrEnum):
    """Areas that can be backed up."""

    WORKSPACE = auto()
    HOME = auto()


# =============================================================================
# Errors
# =============================================================================


class BackupError(Exception):
    """Computer backup operation failed.

    Attributes:
        message: Error description.
        area: Which area was being backed up.
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        area: str = "unknown",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.area = area
        self.retryable = retryable


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Result of a computer backup operation.

    Attributes:
        sha256: SHA256 fingerprint of the backup archive.
        area: Which area was backed up.
        file_count: Number of files included in backup.
        total_bytes: Total size of backup archive.
        excluded_patterns: Patterns that were excluded.
        archive_path: Path to the archive in sandbox (for staging out).
    """

    sha256: str
    area: BackupArea
    file_count: int
    total_bytes: int
    excluded_patterns: tuple[str, ...]
    archive_path: str


@dataclass(frozen=True, slots=True)
class BackupConfig:
    """Configuration for backup operations.

    Attributes:
        workspace_dir: Path to workspace directory.
        home_dir: Path to home directory.
        temp_dir: Path for temporary files.
        credential_exclusions: Patterns for credential files to exclude.
        cache_exclusions: Patterns for cache directories to exclude.
    """

    workspace_dir: str = "/workspace"
    home_dir: str = "/root"
    temp_dir: str = "/tmp"
    credential_exclusions: tuple[str, ...] = CREDENTIAL_EXCLUSIONS
    cache_exclusions: tuple[str, ...] = CACHE_EXCLUSIONS


# =============================================================================
# Abstract Base
# =============================================================================


class BackupOps(ABC):
    """Abstract base for backup operations.

    Implementations execute tar commands inside a sandbox to
    create compressed archives of directories.
    """

    __slots__ = ()

    @abstractmethod
    async def backup_computer(
        self,
        area: BackupArea,
        *,
        exclude_credentials: bool = True,
        exclude_caches: bool = True,
        include_paths: tuple[str, ...] | None = None,
        timeout: float | None = None,
    ) -> BackupResult:
        """Create a backup of the specified area.

        Uses tar with find -print0 for safe filename handling.

        Args:
            area: Area to backup (WORKSPACE or HOME).
            exclude_credentials: Exclude credential files (mandatory for security).
            exclude_caches: Exclude cache directories (reduces size).
            include_paths: If specified, only include these paths.
            timeout: Operation timeout in seconds.

        Returns:
            BackupResult with archive path and fingerprint.

        Raises:
            BackupError: If backup fails.
        """
        ...


# =============================================================================
# Implementation
# =============================================================================


class ExecBackup(BackupOps):
    """Backup via exec(tar) commands.

    Matches QM's createExecBackup pattern:
    - Uses find -print0 | tar for safe filename handling
    - Excludes credentials and caches by default
    - Produces SHA256 fingerprint for deduplication
    """

    __slots__ = ("_exec_fn", "_config")

    def __init__(
        self,
        *,
        exec_fn: Callable[[list[str], float], Awaitable[ExecResult]],
        config: BackupConfig | None = None,
    ) -> None:
        """Initialize backup operations.

        Args:
            exec_fn: Function to execute commands in sandbox.
                     Signature: (cmd, timeout) -> ExecResult
            config: Backup configuration.
        """
        self._exec_fn = exec_fn
        self._config = config or BackupConfig()

    def _build_exclusion_patterns(
        self,
        *,
        exclude_credentials: bool,
        exclude_caches: bool,
    ) -> list[str]:
        """Build find exclusion patterns."""
        patterns: list[str] = []

        if exclude_credentials:
            patterns.extend(self._config.credential_exclusions)

        if exclude_caches:
            patterns.extend(self._config.cache_exclusions)

        return patterns

    def _build_find_prune_expr(self, patterns: list[str]) -> str:
        """Build find -prune expression for exclusions."""
        if not patterns:
            return ""

        # Build: ( -name "pattern1" -o -name "pattern2" ) -prune -o
        conditions = []
        for pattern in patterns:
            if "/" in pattern:
                # Path pattern - use -path
                conditions.append(f'-path "*/{pattern}"')
            else:
                # Name pattern - use -name
                conditions.append(f'-name "{pattern}"')

        prune_expr = " -o ".join(conditions)
        return f"\\( {prune_expr} \\) -prune -o"

    async def backup_computer(
        self,
        area: BackupArea,
        *,
        exclude_credentials: bool = True,
        exclude_caches: bool = True,
        include_paths: tuple[str, ...] | None = None,
        timeout: float | None = None,
    ) -> BackupResult:
        """Create backup using tar with find -print0."""
        timeout = timeout or BACKUP_TIMEOUT_S

        # Determine base path
        if area == BackupArea.WORKSPACE:
            base_path = self._config.workspace_dir
        else:
            base_path = self._config.home_dir

        # Build exclusion patterns
        exclusions = self._build_exclusion_patterns(
            exclude_credentials=exclude_credentials,
            exclude_caches=exclude_caches,
        )

        # If backing up home, also exclude workspace to avoid duplication
        if area == BackupArea.HOME:
            workspace_rel = self._config.workspace_dir.replace(
                self._config.home_dir + "/", ""
            )
            if not workspace_rel.startswith("/"):
                exclusions.append(workspace_rel)

        # Generate archive path
        archive_id = uuid.uuid4().hex[:12]
        archive_path = f"{self._config.temp_dir}/backup-{area.value}-{archive_id}.tar"

        # Build find command with exclusions
        prune_expr = self._build_find_prune_expr(exclusions)

        if include_paths:
            # Only include specified paths
            path_args = " ".join(f'"{p}"' for p in include_paths)
            find_cmd = f"find {path_args} -type f -print0"
        else:
            # Find all files with exclusions
            find_cmd = f'cd "{base_path}" && find . {prune_expr} -type f -print0'

        # Build full script: find | tar | count
        script = f"""
set -e
{find_cmd} | tar --null -T - -cf "{archive_path}" 2>/dev/null || true
if [ ! -f "{archive_path}" ]; then
    # Empty archive if no files found
    tar -cf "{archive_path}" --files-from /dev/null
fi
sha256sum "{archive_path}" | cut -d' ' -f1
stat -c%s "{archive_path}"
tar -tf "{archive_path}" 2>/dev/null | wc -l || echo 0
"""

        result = await self._exec_fn(["sh", "-c", script], timeout)

        if not result.success:
            raise BackupError(
                f"backup failed (exit {result.exit_code}): {result.stderr_text[:200]}",
                area=str(area),
                retryable=False,
            )

        # Parse output
        lines = result.stdout_text.strip().split("\n")
        if len(lines) < 3:
            raise BackupError(
                f"unexpected backup output: {result.stdout_text[:200]}",
                area=str(area),
                retryable=False,
            )

        sha256 = lines[0].strip()
        total_bytes = int(lines[1].strip())
        file_count = int(lines[2].strip())

        _log.debug(
            "backed up %s: %d files, %d bytes, sha256=%s...",
            area.value,
            file_count,
            total_bytes,
            sha256[:16],
        )

        return BackupResult(
            sha256=sha256,
            area=area,
            file_count=file_count,
            total_bytes=total_bytes,
            excluded_patterns=tuple(exclusions),
            archive_path=archive_path,
        )


# =============================================================================
# Factory
# =============================================================================


def create_exec_backup(
    *,
    exec_fn: Callable[[list[str], float], Awaitable[ExecResult]],
    config: BackupConfig | None = None,
) -> BackupOps:
    """Create backup operations using exec(tar).

    Factory function that matches QM's createExecBackup pattern.

    Args:
        exec_fn: Function to execute commands in sandbox.
                 Signature: (cmd: list[str], timeout: float) -> ExecResult
        config: Backup configuration.

    Returns:
        BackupOps instance ready for backup operations.

    Example:
        >>> backup = create_exec_backup(exec_fn=sandbox.exec)
        >>> result = await backup.backup_computer(BackupArea.WORKSPACE)
        >>> print(f"Backed up {result.file_count} files")
    """
    return ExecBackup(exec_fn=exec_fn, config=config)
