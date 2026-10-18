"""Shared fixtures for D10 blind sandbox isolation acceptance."""

from __future__ import annotations

import pytest

from supervisor.seams.virtual_keys import MintedVirtualKey
from tests.unit.test_docker import FakeDockerClient
from tests.unit.test_e2b import FakeE2BClient


class RecordingVirtualKeyClient:
    """Minimal VK client for terminate/revoke matrix rows."""

    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.minted: list[str] = []

    def mint(
        self,
        *,
        tenant_id: str,
        session_id: str,
        principal_id: str,
        allowed_models: list[str] | None = None,
        ttl_s: int = 3600,
        max_budget_usd: float = 5.0,
        scope_id: str | None = None,
    ) -> MintedVirtualKey:
        self.minted.append(session_id)
        return MintedVirtualKey(
            key_id=f"vk-{session_id[:8]}",
            token=f"vk_{session_id[:8]}",
            expires_at=9999999999.0,
            session_id=session_id,
            tenant_id=tenant_id,
        )

    def revoke_session(self, session_id: str) -> int:
        self.revoked.append(session_id)
        return 1


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.fixture
def fake_e2b() -> FakeE2BClient:
    return FakeE2BClient()


@pytest.fixture
def recording_vk() -> RecordingVirtualKeyClient:
    return RecordingVirtualKeyClient()
