"""A session's files beyond its workspace: attachments, run inputs and artifacts.

The boundaries (each owns one thing):

* the **workspace** owns files and access; a **version** is durable content;
* an **attachment** is a workspace file used in one conversation, pinned to a version: its runs
  see exactly that version, read-only, in ``.attachments/``;
* a **run** records what it started from (``run_inputs``): every attachment and every workspace
  file it could read, by version and SHA-256;
* an **artifact** is a file a run made for the user in ``.outputs/``. The supervisor (never the
  agent) collects it at the run's end: it reads the file, checks its size, stores it under a
  private key and records which run made it. Saving it to a workspace folder makes a version
  whose ``source`` names the artifact;
* the **sandbox** is temporary: the two folders above live in it but are never copied into the
  workspace (``ScopeMirrorTree``).
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from sqlalchemy import select

from supervisor.db.models import ArtifactRow, RunInputRow, SessionAttachmentRow
from supervisor.seams.services.session_workspace import tree_for_sandbox
from supervisor.seams.services.workspace_store import new_object_key, normalize_workspace_path

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from supervisor.sandbox.base import Sandbox
    from supervisor.seams.services.workspace_store import WorkspaceFileRecord, WorkspaceStore

logger = logging.getLogger(__name__)

ATTACHMENTS_DIR: Final = ".attachments"
OUTPUTS_DIR: Final = ".outputs"
MAX_ARTIFACT_BYTES: Final = 100 * 1024 * 1024
MAX_ARTIFACTS_PER_RUN: Final = 50


class FileNotInWorkspace(LookupError):
    """The path (or that version of it) is not in the workspace."""


class ArtifactNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Attachment:
    id: str
    scope_id: str
    path: str
    version: int
    content_sha256: str
    size_bytes: int
    mime_type: str | None
    name: str
    attached_by: str | None
    created_at: datetime

    @property
    def sandbox_path(self) -> str:
        return f"{ATTACHMENTS_DIR}/{self.name}"


@dataclass(frozen=True)
class RunInput:
    kind: str
    scope_id: str
    path: str
    version: int
    content_sha256: str


@dataclass(frozen=True)
class Artifact:
    id: str
    session_id: str
    turn_id: str
    name: str
    content_sha256: str
    object_key: str
    size_bytes: int
    mime_type: str | None
    created_at: datetime
    saved_scope_id: str | None
    saved_path: str | None
    saved_version: int | None


def _attachment(row: SessionAttachmentRow) -> Attachment:
    return Attachment(
        id=row.id, scope_id=row.scope_id, path=row.path, version=row.version,
        content_sha256=row.content_sha256, size_bytes=int(row.size_bytes), mime_type=row.mime_type,
        name=row.name, attached_by=row.attached_by, created_at=row.created_at,
    )


def _artifact(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=row.id, session_id=row.session_id, turn_id=row.turn_id, name=row.name,
        content_sha256=row.content_sha256, object_key=row.object_key, size_bytes=int(row.size_bytes),
        mime_type=row.mime_type, created_at=row.created_at, saved_scope_id=row.saved_scope_id,
        saved_path=row.saved_path, saved_version=row.saved_version,
    )


def _free_name(wanted: str, taken: set[str]) -> str:
    if wanted not in taken:
        return wanted
    stem, ext = posixpath.splitext(wanted)
    n = 2
    while f"{stem}-{n}{ext}" in taken:
        n += 1
    return f"{stem}-{n}{ext}"


class SessionFiles:
    """Attachments, run inputs and artifacts for sessions, over the database and the store."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], store: WorkspaceStore) -> None:
        self._db = session_factory
        self._store = store
        self._notes: dict[str, str] = {}

    @property
    def _objects(self):  # type: ignore[no-untyped-def]
        objects = getattr(self._store, "objects", None)
        if objects is None:
            raise RuntimeError("the workspace store has no object storage")
        return objects

    # --- attachments ------------------------------------------------------------------

    async def attach(
        self, session_id: str, scope_id: str, path: str, *, version: int | None, attached_by: str | None
    ) -> Attachment:
        """Pin ``path`` (its current version, or ``version``) to the session. Attaching a path
        again replaces the earlier pin (another version of the same file)."""
        path = normalize_workspace_path(path)
        if version is None:
            live = await self._store.stat(scope_id, path)
            if live is None:
                raise FileNotInWorkspace(f"{path} is not in the workspace")
            version = live.version
        found = await self._store.get_version(scope_id, path, version)
        if found is None:
            raise FileNotInWorkspace(f"{path} has no version {version}")
        now = datetime.now(UTC)
        async with self._db() as db:
            active = (await db.scalars(
                select(SessionAttachmentRow).where(
                    SessionAttachmentRow.session_id == session_id, SessionAttachmentRow.removed_at.is_(None)
                )
            )).all()
            for row in active:
                if (row.scope_id, row.path) == (scope_id, path):
                    row.removed_at = now
            taken = {r.name for r in active if r.removed_at is None}
            row = SessionAttachmentRow(
                id=uuid4().hex, session_id=session_id, scope_id=scope_id, path=path, version=version,
                content_sha256=found.content_sha256, object_key=found.object_key,
                size_bytes=found.size_bytes, mime_type=found.mime_type,
                name=_free_name(posixpath.basename(path), taken), attached_by=attached_by, created_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        await self.refresh_note(session_id)
        return _attachment(row)

    async def attachments(self, session_id: str) -> list[Attachment]:
        async with self._db() as db:
            rows = (await db.scalars(
                select(SessionAttachmentRow)
                .where(SessionAttachmentRow.session_id == session_id, SessionAttachmentRow.removed_at.is_(None))
                .order_by(SessionAttachmentRow.created_at)
            )).all()
            return [_attachment(r) for r in rows]

    async def detach(self, session_id: str, attachment_id: str) -> bool:
        async with self._db() as db:
            row = await db.get(SessionAttachmentRow, attachment_id)
            if row is None or row.session_id != session_id or row.removed_at is not None:
                return False
            row.removed_at = datetime.now(UTC)
            await db.commit()
        await self.refresh_note(session_id)
        return True

    async def deliver(self, session_id: str, sandbox: Sandbox) -> int:
        """Put the session's attachments in ``.attachments/`` (exactly those, read-only)."""
        tree = tree_for_sandbox(sandbox)
        current = await self.attachments(session_id)
        for old in await tree.list_paths():
            if old.startswith(f"{ATTACHMENTS_DIR}/"):
                await _make_writable(sandbox, old)
                await tree.delete_path(old)
        for a in current:
            data = await self._objects.get(await self._key_of(a))
            await tree.write_bytes(a.sandbox_path, data)
        if current:
            await _make_read_only(sandbox)
        return len(current)

    async def _key_of(self, a: Attachment) -> str:
        async with self._db() as db:
            row = await db.get(SessionAttachmentRow, a.id)
            assert row is not None
            return row.object_key

    async def refresh_note(self, session_id: str) -> str:
        """What the model is told about this session's files (cached for its next call)."""
        current = await self.attachments(session_id)
        lines = [
            (f"Files you make for the user go in {OUTPUTS_DIR}/ (in the workspace folder): they are "
            "collected when your turn ends and shown to the user, who can save them.")
        ]
        if current:
            listed = ", ".join(f"{a.sandbox_path} ({a.path}, version {a.version})" for a in current)
            lines.insert(0, f"The user attached these files, read-only, in {ATTACHMENTS_DIR}/: {listed}.")
        note = " ".join(lines)
        self._notes[session_id] = note
        return note

    async def context_note(self, session_id: str) -> str:
        if (note := self._notes.get(session_id)) is not None:
            return note
        return await self.refresh_note(session_id)

    # --- run inputs -------------------------------------------------------------------

    async def snapshot(self, session_id: str, turn_id: str, scope_id: str | None) -> int:
        """Record what this run starts from: its attachments and the workspace's files."""
        rows: list[RunInputRow] = []
        now = datetime.now(UTC)
        for a in await self.attachments(session_id):
            rows.append(RunInputRow(
                id=uuid4().hex, session_id=session_id, turn_id=turn_id, kind="attachment",
                scope_id=a.scope_id, path=a.path, version=a.version, content_sha256=a.content_sha256,
                created_at=now,
            ))
        if scope_id is not None:
            files: list[WorkspaceFileRecord] = await self._store.list_files(scope_id)
            rows.extend(
                RunInputRow(
                    id=uuid4().hex, session_id=session_id, turn_id=turn_id, kind="workspace",
                    scope_id=scope_id, path=f.path, version=f.version, content_sha256=f.content_sha256,
                    created_at=now,
                )
                for f in files
            )
        if rows:
            async with self._db() as db:
                db.add_all(rows)
                await db.commit()
        await self.refresh_note(session_id)
        return len(rows)

    async def run_inputs(self, session_id: str, turn_id: str) -> list[RunInput]:
        async with self._db() as db:
            rows = (await db.scalars(
                select(RunInputRow)
                .where(RunInputRow.session_id == session_id, RunInputRow.turn_id == turn_id)
                .order_by(RunInputRow.kind, RunInputRow.path)
            )).all()
            return [RunInput(r.kind, r.scope_id, r.path, r.version, r.content_sha256) for r in rows]

    # --- artifacts --------------------------------------------------------------------

    async def collect(self, session_id: str, turn_id: str, sandbox: Sandbox) -> list[Artifact]:
        """The trusted collector: what the run left in ``.outputs/`` that is new or changed
        becomes an artifact (read, size-checked, stored under a private key, recorded)."""
        import hashlib

        tree = tree_for_sandbox(sandbox)
        paths = [p for p in await tree.list_paths() if p.startswith(f"{OUTPUTS_DIR}/")]
        if not paths:
            return []
        known: dict[str, str] = {}
        async with self._db() as db:
            for row in (await db.scalars(
                select(ArtifactRow).where(ArtifactRow.session_id == session_id).order_by(ArtifactRow.created_at)
            )).all():
                known[row.name] = row.content_sha256  # the latest one of each name wins
        made: list[ArtifactRow] = []
        for path in sorted(paths)[:MAX_ARTIFACTS_PER_RUN]:
            name = path.removeprefix(f"{OUTPUTS_DIR}/")
            try:
                data = await tree.read_bytes(path)
            except FileNotFoundError:
                continue
            if len(data) > MAX_ARTIFACT_BYTES:
                logger.warning("output %s of session %s is too large to collect (%d bytes)", name, session_id, len(data))
                continue
            digest = hashlib.sha256(data).hexdigest()
            if known.get(name) == digest:
                continue  # unchanged since a run already collected it
            key = f"sessions/{session_id}/artifacts/{uuid4().hex}"
            await self._objects.put(key, data, content_sha256=digest)
            made.append(ArtifactRow(
                id=uuid4().hex, session_id=session_id, turn_id=turn_id, name=name, content_sha256=digest,
                object_key=key, size_bytes=len(data),
                mime_type=mimetypes.guess_type(name)[0], created_at=datetime.now(UTC),
            ))
        if len(paths) > MAX_ARTIFACTS_PER_RUN:
            logger.warning("session %s left %d outputs; collected the first %d", session_id, len(paths), MAX_ARTIFACTS_PER_RUN)
        if made:
            async with self._db() as db:
                db.add_all(made)
                await db.commit()
        return [_artifact(r) for r in made]

    async def artifacts(self, session_id: str) -> list[Artifact]:
        async with self._db() as db:
            rows = (await db.scalars(
                select(ArtifactRow).where(ArtifactRow.session_id == session_id).order_by(ArtifactRow.created_at)
            )).all()
            return [_artifact(r) for r in rows]

    async def artifact(self, session_id: str, artifact_id: str) -> Artifact:
        async with self._db() as db:
            row = await db.get(ArtifactRow, artifact_id)
            if row is None or row.session_id != session_id:
                raise ArtifactNotFound(artifact_id)
            return _artifact(row)

    async def artifact_bytes(self, session_id: str, artifact_id: str) -> tuple[Artifact, bytes]:
        found = await self.artifact(session_id, artifact_id)
        return found, await self._objects.get(found.object_key)

    async def save(
        self,
        session_id: str,
        artifact_id: str,
        *,
        scope_id: str,
        path: str,
        author: str | None,
        expected_version: int | None = None,
    ) -> tuple[Artifact, WorkspaceFileRecord]:
        """Save an artifact into a workspace as a new version of ``path`` (its source recorded)."""
        found = await self.artifact(session_id, artifact_id)
        path = normalize_workspace_path(path)
        key = await self._store.find_object(scope_id, found.content_sha256, found.size_bytes)
        if key is None:
            if (check_room := getattr(self._store, "check_room", None)) is not None:
                await check_room(scope_id, None, found.size_bytes)  # before the bytes are copied
            key = new_object_key(scope_id)
            await self._objects.copy(found.object_key, key, content_sha256=found.content_sha256)
        record = await self._store.commit_object(
            scope_id, path, content_sha256=found.content_sha256, size_bytes=found.size_bytes,
            object_key=key, mime_type=found.mime_type, expected_version=expected_version,
            author=author, source=f"artifact:{found.id}",
        )
        async with self._db() as db:
            row = await db.get(ArtifactRow, found.id)
            assert row is not None
            row.saved_scope_id, row.saved_path, row.saved_version = scope_id, record.path, record.version
            await db.commit()
            await db.refresh(row)
            return _artifact(row), record


async def _make_read_only(sandbox: Sandbox) -> None:
    try:
        await sandbox.exec(["chmod", "-R", "a-w", ATTACHMENTS_DIR])
    except Exception:
        logger.debug("could not make attachments read-only in %s", sandbox.id, exc_info=True)


async def _make_writable(sandbox: Sandbox, path: str) -> None:
    try:
        await sandbox.exec(["chmod", "-R", "u+w", ATTACHMENTS_DIR])
    except Exception:
        logger.debug("could not make %s writable in %s", path, sandbox.id, exc_info=True)
