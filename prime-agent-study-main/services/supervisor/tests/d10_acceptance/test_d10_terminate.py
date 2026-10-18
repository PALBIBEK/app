"""D10 matrix T13 — terminate destroys sandbox and revokes VK."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.sandbox.local_docker import LocalDockerProvider
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry
from tests.d10_acceptance.conftest import RecordingVirtualKeyClient
from tests.support.session_env import make_catalog
from tests.unit.test_docker import FakeDockerClient


class DestroyTrackingDocker(LocalDockerProvider):
    destroyed: list[str] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        sb = await super().create(**kwargs)
        orig_destroy = sb.destroy

        async def tracked_destroy() -> None:
            DestroyTrackingDocker.destroyed.append(sb.info.id)
            await orig_destroy()

        sb.destroy = tracked_destroy  # type: ignore[method-assign]
        return sb


@pytest.mark.asyncio
async def test_t13_terminate_destroys_sandbox_and_revokes_vk(
    tmp_path, fake_docker: FakeDockerClient, recording_vk: RecordingVirtualKeyClient
) -> None:
    DestroyTrackingDocker.destroyed = []
    provider = DestroyTrackingDocker(client=fake_docker)
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd10.db'}") as (_, factory):
        manager = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=make_catalog(),
            sandbox_provider=provider,
            virtual_key_client=recording_vk,  # type: ignore[arg-type]
            live_queue_max=8,
        )
        with patch(
            "supervisor.sandbox.resolve.create_sandbox_provider",
            return_value=provider,
        ), patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
            sid = await manager.create(
                tenant_id="t1",
                harness_version="coding-v1",
                runtime_name="echo",
            )
        assert len(DestroyTrackingDocker.destroyed) == 0
        await manager.terminate(sid)
        assert sid in recording_vk.revoked
        assert len(DestroyTrackingDocker.destroyed) == 1
