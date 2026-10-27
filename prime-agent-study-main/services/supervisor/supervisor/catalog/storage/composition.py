"""``CompositionCatalog``: every artifact version this process knows, and who owns each name.

It is a cache. The ``catalog_artifacts`` table is the source of truth
(``catalog.repository``): the API writes there on publish and deprecate, and a
process syncs what a session is about to pin before resolving it, so replicas
agree. Reads are synchronous and in memory because resolution sits on the
session-create and bind paths.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import TYPE_CHECKING

from supervisor.catalog.core.digests import content_digest, is_unset_digest, verify_digest
from supervisor.catalog.core.errors import CatalogOwnershipRequired, NotOffered
from supervisor.catalog.core.types import ArtifactKind, ArtifactMetadata, ArtifactStatus
from supervisor.catalog.storage.converters import kind_of, with_digest, with_metadata, with_status

if TYPE_CHECKING:
    from supervisor.catalog.core.types import Artifact

__all__ = ["SYSTEM_OWNER", "CompositionCatalog"]

SYSTEM_OWNER = "system"
"""Owner of shipped artifacts: every tenant may use them, none may change them."""


def _kind(kind: ArtifactKind | str) -> ArtifactKind:
    return ArtifactKind(kind)


class CompositionCatalog:
    """Artifacts by ``(kind, name, digest)``, versions by ``(kind, name)``, and owners by name."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[ArtifactKind, str, str], Artifact] = {}
        self._versions: dict[tuple[ArtifactKind, str], list[str]] = defaultdict(list)
        self._by_digest: dict[str, tuple[ArtifactKind, str]] = {}
        self._owners: dict[tuple[ArtifactKind, str], str] = {}

    # --- tenancy -------------------------------------------------------------

    def owner(self, kind: ArtifactKind | str, name: str) -> str | None:
        """The tenant that owns ``name`` (``SYSTEM_OWNER`` for shipped ones), if known."""
        return self._owners.get((_kind(kind), name))

    def claim(self, kind: ArtifactKind | str, name: str, owner: str) -> None:
        """Record ``owner`` for ``name``; a name owned by anyone else is refused."""
        key = (_kind(kind), name)
        current = self._owners.setdefault(key, owner)
        if current != owner:
            raise CatalogOwnershipRequired(
                principal_id=owner, artifact_kind=key[0].value, artifact_id=name, owner_id=current
            )

    def visible_to(self, kind: ArtifactKind | str, name: str, tenant_id: str) -> bool:
        """Whether ``tenant_id`` may see and use ``name``: shipped, or its own."""
        return self.owner(kind, name) in (SYSTEM_OWNER, tenant_id, None)

    # --- writes --------------------------------------------------------------

    def offer(
        self,
        artifact: Artifact,
        *,
        owner: str = SYSTEM_OWNER,
        metadata: ArtifactMetadata | None = None,
    ) -> Artifact:
        """Store ``artifact`` as offered, under ``owner``; returns it with its digest.

        A missing or placeholder digest is computed; a claimed one must match the
        content (``DigestMismatch``): sessions pin by digest, so a digest that did
        not hash to its body would let anyone rewrite pinned content. ``metadata``
        (who published it, when: the database row's) replaces whatever it had.
        """
        kind = kind_of(artifact)
        self.claim(kind, artifact.name, owner)
        body = artifact.body_for_digest()
        if is_unset_digest(artifact.digest):
            artifact = with_digest(artifact, content_digest(body))
        else:
            verify_digest(body, artifact.digest)
        if artifact.status != ArtifactStatus.OFFERED:
            artifact = with_status(artifact, ArtifactStatus.OFFERED)
        if metadata is not None or artifact.metadata is not None:
            artifact = with_metadata(artifact, metadata or artifact.metadata)

        self._by_key[(kind, artifact.name, artifact.digest)] = artifact
        versions = self._versions[(kind, artifact.name)]
        if artifact.digest not in versions:
            versions.append(artifact.digest)
        self._by_digest[artifact.digest] = (kind, artifact.name)
        return artifact

    def deprecate(
        self,
        kind: ArtifactKind | str,
        artifact_id: str,
        digest: str,
        *,
        metadata: ArtifactMetadata | None = None,
    ) -> Artifact:
        """Mark one version deprecated (sessions pinned to it keep running); returns it."""
        artifact = with_status(self.get(kind, artifact_id, digest), ArtifactStatus.DEPRECATED)
        if metadata is not None:
            artifact = with_metadata(artifact, metadata)
        self._by_key[(_kind(kind), artifact_id, digest)] = artifact
        return artifact

    # --- reads ---------------------------------------------------------------

    def get(
        self,
        kind: ArtifactKind | str,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> Artifact:
        """One version; ``NotOffered`` if absent (or, with ``require_offered``, deprecated)."""
        k = _kind(kind)
        artifact = self._by_key.get((k, artifact_id, digest))
        if artifact is None:
            raise NotOffered(kind=k.value, artifact_id=artifact_id, digest=digest)
        if require_offered and artifact.status != ArtifactStatus.OFFERED:
            raise NotOffered(
                kind=k.value, artifact_id=artifact_id, digest=digest, status=artifact.status.value
            )
        return artifact

    def get_as[A: Artifact](
        self,
        cls: type[A],
        kind: ArtifactKind | str,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> A:
        """``get``, checked to be a ``cls`` (a kind always maps to one artifact type)."""
        artifact = self.get(kind, artifact_id, digest, require_offered=require_offered)
        if not isinstance(artifact, cls):
            raise TypeError(f"{kind} {artifact_id!r} is a {type(artifact).__name__}")
        return artifact

    def artifacts(
        self,
        kind: ArtifactKind | str | None = None,
        *,
        status: ArtifactStatus | str | None = None,
    ) -> Iterator[Artifact]:
        """Every version held (of ``kind``, in ``status``), in the order they were offered."""
        k = _kind(kind) if kind is not None else None
        s = ArtifactStatus(status) if status is not None else None
        for (art_kind, _, _), artifact in self._by_key.items():
            if (k is None or art_kind == k) and (s is None or artifact.status == s):
                yield artifact

    def list_artifacts(
        self,
        kind: ArtifactKind | str,
        *,
        status: ArtifactStatus | str | None = None,
    ) -> list[Artifact]:
        return list(self.artifacts(kind, status=status))

    def list_versions(self, kind: ArtifactKind | str, artifact_id: str) -> list[str]:
        """Every digest of ``artifact_id``, oldest first."""
        return list(self._versions.get((_kind(kind), artifact_id), ()))

    def latest(self, kind: ArtifactKind | str, artifact_id: str) -> Artifact | None:
        """The newest version of ``artifact_id`` (offered or not), if any."""
        versions = self.list_versions(kind, artifact_id)
        return self.get(kind, artifact_id, versions[-1]) if versions else None

    def get_by_digest(self, digest: str) -> tuple[ArtifactKind, str] | None:
        """Which ``(kind, name)`` a digest belongs to."""
        return self._by_digest.get(digest)

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, digest: str) -> bool:
        return digest in self._by_digest
