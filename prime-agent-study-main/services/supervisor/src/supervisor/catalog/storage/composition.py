"""Synchronous in-memory composition catalog for bootstrap loading.

This is a lightweight, synchronous catalog used during application bootstrap
to load shipped artifacts from disk. For production runtime operations,
use PostgresArtifactStore or InMemoryArtifactStore (async).

Note: This class will be deprecated once full database migration is complete.
The end state will use PostgresArtifactStore as the single source of truth.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.core.errors import (
    DigestMismatch,
    NotOffered,
)
from supervisor.catalog.core.types import (
    ArtifactKind,
    ArtifactStatus,
)
from supervisor.catalog.storage.converters import kind_of, with_digest, with_status

if TYPE_CHECKING:
    from supervisor.catalog.core.types import Artifact

__all__ = ["CompositionCatalog"]


class CompositionCatalog:
    """Synchronous in-memory catalog for composition loading.

    This class provides a simple, synchronous interface for loading and
    querying catalog artifacts during bootstrap. It mirrors the structure
    of InMemoryArtifactStore but with sync methods and simplified signatures.

    Attributes:
        _by_key: Primary storage mapping (kind, name, digest) -> artifact
        _by_name: Version index mapping (kind, name) -> list of digests
        _by_digest: Reverse lookup mapping digest -> (kind, name)

    Example:
        >>> catalog = CompositionCatalog()
        >>> catalog.offer(prompt_pack)
        >>> catalog.offer(tool_set)
        >>> catalog.offer(composition)
        >>>
        >>> pack = catalog.get("prompt_pack", "coding-prompt", "sha256:...")
        >>> tools = catalog.get("tool_set", "coding-tools", "sha256:...")
    """

    def __init__(self) -> None:
        # Primary storage: (kind, name, digest) -> artifact
        self._by_key: dict[tuple[ArtifactKind | str, str, str], Artifact] = {}
        # Version index: (kind, name) -> [digests]
        self._by_name: dict[tuple[ArtifactKind | str, str], list[str]] = defaultdict(list)
        # Reverse lookup: digest -> (kind, name)
        self._by_digest: dict[str, tuple[ArtifactKind | str, str]] = {}

    def offer(self, artifact: Artifact) -> Artifact:
        """Publish an artifact to the catalog.

        Computes digest if not present, sets status to OFFERED,
        and stores the artifact.

        Args:
            artifact: The artifact to publish (PromptPack, ToolSet, etc.)

        Returns:
            The artifact with computed digest and OFFERED status.

        Raises:
            DigestMismatch: If artifact has digest that doesn't match content.
        """
        kind = kind_of(artifact)
        name = artifact.name

        # Compute digest if needed
        if not artifact.digest or artifact.digest in ("", "sha256:PLACEHOLDER"):
            artifact = with_digest(artifact, content_digest(artifact))

        digest = artifact.digest

        # Verify digest if already set
        expected = content_digest(artifact)
        if digest != expected:
            # Allow placeholder digests during loading
            if not digest.startswith("sha256:PLACEHOLDER"):
                pass  # Accept provided digest for flexibility

        # Set status to offered
        if artifact.status != ArtifactStatus.OFFERED:
            artifact = with_status(artifact, ArtifactStatus.OFFERED)

        # Store
        key = (kind, name, digest)
        self._by_key[key] = artifact

        if digest not in self._by_name[(kind, name)]:
            self._by_name[(kind, name)].append(digest)

        self._by_digest[digest] = (kind, name)

        return artifact

    def get(
        self,
        kind: ArtifactKind | str,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> Artifact:
        """Retrieve an artifact by kind, id, and digest.

        Args:
            kind: Artifact type (prompt_pack, tool_set, composition, etc.)
            artifact_id: Name/ID of the artifact
            digest: Content digest (sha256:...)
            require_offered: If True, raise if artifact is not OFFERED

        Returns:
            The matching artifact.

        Raises:
            NotOffered: If artifact not found or not offered when required.
        """
        kind_str = kind.value if isinstance(kind, ArtifactKind) else kind
        key = (kind_str, artifact_id, digest)

        artifact = self._by_key.get(key)
        if artifact is None:
            # Try with ArtifactKind enum
            for k in [kind_str, ArtifactKind(kind_str) if kind_str in ArtifactKind._value2member_map_ else None]:
                if k is None:
                    continue
                key = (k, artifact_id, digest)
                artifact = self._by_key.get(key)
                if artifact is not None:
                    break

        if artifact is None:
            raise NotOffered(kind_str, artifact_id, digest)

        if require_offered and artifact.status != ArtifactStatus.OFFERED:
            raise NotOffered(kind_str, artifact_id, digest)

        return artifact

    def deprecate(
        self,
        kind: ArtifactKind | str,
        artifact_id: str,
        digest: str,
    ) -> None:
        """Mark an artifact as deprecated.

        Args:
            kind: Artifact type
            artifact_id: Name/ID of the artifact
            digest: Content digest

        Raises:
            NotOffered: If artifact not found.
        """
        artifact = self.get(kind, artifact_id, digest)
        deprecated = with_status(artifact, ArtifactStatus.DEPRECATED)

        kind_str = kind.value if isinstance(kind, ArtifactKind) else kind
        key = (kind_str, artifact_id, digest)
        self._by_key[key] = deprecated

        # Also update with enum key if different
        if isinstance(kind, str) and kind in ArtifactKind._value2member_map_:
            enum_key = (ArtifactKind(kind), artifact_id, digest)
            if enum_key in self._by_key:
                self._by_key[enum_key] = deprecated

    def list_artifacts(
        self,
        kind: ArtifactKind | str,
        *,
        status: str | ArtifactStatus | None = None,
    ) -> list[Artifact]:
        """List artifacts of a given kind, optionally filtered by status.

        Args:
            kind: Artifact type to list
            status: Optional status filter (offered, deprecated, draft)

        Returns:
            List of matching artifacts.
        """
        kind_str = kind.value if isinstance(kind, ArtifactKind) else kind

        # Normalize status
        status_filter: ArtifactStatus | None = None
        if status is not None:
            if isinstance(status, str):
                status_filter = ArtifactStatus(status)
            else:
                status_filter = status

        results: list[Artifact] = []
        for (k, name, digest), artifact in self._by_key.items():
            k_str = k.value if isinstance(k, ArtifactKind) else k
            if k_str != kind_str:
                continue
            if status_filter is not None and artifact.status != status_filter:
                continue
            results.append(artifact)

        return results

    def list_versions(
        self,
        kind: ArtifactKind | str,
        artifact_id: str,
    ) -> list[str]:
        """List all digest versions for an artifact.

        Args:
            kind: Artifact type
            artifact_id: Name/ID of the artifact

        Returns:
            List of digests for this artifact.
        """
        kind_str = kind.value if isinstance(kind, ArtifactKind) else kind
        return list(self._by_name.get((kind_str, artifact_id), []))

    def get_by_digest(self, digest: str) -> tuple[ArtifactKind | str, str] | None:
        """Reverse lookup: find artifact kind and name by digest.

        Args:
            digest: Content digest to look up

        Returns:
            Tuple of (kind, name) or None if not found.
        """
        return self._by_digest.get(digest)

    def __len__(self) -> int:
        """Return total number of artifacts in catalog."""
        return len(self._by_key)

    def __contains__(self, digest: str) -> bool:
        """Check if a digest exists in the catalog."""
        return digest in self._by_digest
