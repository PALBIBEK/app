"""Shared fixtures for session-layer tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from supervisor.db.engine import open_db
from supervisor.harness import (
    HarnessCatalog,
    HarnessManifest,
    RecoveryAction,
    RecoveryPolicy,
    ToolClass,
    ToolSpec,
    default_recovery_policy,
)
from supervisor.harness.core.enums import RuntimeMode
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.sandbox.base import (
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxInfo,
    SandboxProvider,
    SnapshotUnsupported,
)
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry


class TrackingProvider(SandboxProvider):
    name = "tracking"

    def __init__(
        self,
        base_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        # Fixtures pass base_dir (LocalProcessProvider-shaped); ignore extras.
        del kwargs
        self._base_dir = base_dir
        self.create_calls: list[dict[str, Any]] = []
        self.resume_calls: list[str] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.create_calls.append(dict(kwargs))
        return _DummySandbox()

    async def resume(self, snapshot_id: str) -> Sandbox:
        self.resume_calls.append(snapshot_id)
        return _DummySandbox()

    async def get(self, sandbox_id: str) -> Sandbox | None:
        return None


class _DummySandbox(Sandbox):
    def __init__(self) -> None:
        self._info = SandboxInfo(id="dummy", provider="tracking", supports_snapshot=True)

    @property
    def info(self) -> SandboxInfo:
        return self._info

    async def spawn(self, cmd, *, cwd=None, env=None) -> ProcessHandle:
        raise NotImplementedError

    async def exec(self, cmd, *, cwd=None, env=None, timeout=None) -> ExecResult:
        raise NotImplementedError

    async def read_file(self, path: str) -> bytes:
        raise NotImplementedError

    async def write_file(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    async def exists(self, path: str) -> bool:
        return False

    async def pause(self) -> str:
        raise SnapshotUnsupported("dummy")

    async def destroy(self) -> None:
        return None

    async def is_alive(self) -> bool:
        return True


def _tools(mapping: dict[str, ToolClass]) -> dict[str, ToolSpec]:
    return {k: ToolSpec(tool_class=v) for k, v in mapping.items()}


def make_manifest(
    version: str,
    *,
    requires_sandbox: bool,
    image_digest: str | None,
    runtime: str = "echo",
    recovery: RecoveryPolicy | None = None,
) -> HarnessManifest:
    policy = recovery or default_recovery_policy()
    tools = _tools(policy.tool_classes)
    return HarnessManifest(
        harness_version=version,
        protocol_version=1,
        runtime=runtime,
        requires_sandbox=requires_sandbox,
        mode=RuntimeMode.STICKY if requires_sandbox else RuntimeMode.STATELESS,
        image_digest=image_digest,
        extension_bundle_uri=None,
        tools=tools,
        recovery=policy,
    )


def make_catalog() -> HarnessCatalog:
    return HarnessCatalog(
        {
            "kg-v1": make_manifest(
                "kg-v1", requires_sandbox=False, image_digest=None
            ),
            "githi-v1": make_manifest(
                "githi-v1",
                requires_sandbox=True,
                image_digest="sha256:" + "b" * 64,
                runtime="githi",
            ),
            "coding-v1": make_manifest(
                "coding-v1",
                requires_sandbox=True,
                image_digest="sha256:" + "a" * 64,
            ),
            "coding-v2": make_manifest(
                "coding-v2",
                requires_sandbox=True,
                image_digest="sha256:" + "b" * 64,
            ),
        }
    )


@pytest.fixture
async def session_env(tmp_path: Path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 's.db'}") as (engine, factory):
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        registry = SessionRegistry()
        catalog = make_catalog()
        provider = TrackingProvider()
        manager = SessionManager(
            session_factory=factory,
            writer=writer,
            reader=reader,
            registry=registry,
            catalog=catalog,
            sandbox_provider=provider,
            live_queue_max=8,
        )
        yield {
            "manager": manager,
            "writer": writer,
            "reader": reader,
            "registry": registry,
            "catalog": catalog,
            "provider": provider,
            "factory": factory,
        }
