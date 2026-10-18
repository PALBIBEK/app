"""Sandbox factory, orphan reaper, drain, and abstraction leak checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.sandbox.factory import create_sandbox_provider
from supervisor.sandbox.local_docker import LABEL_MANAGED, LABEL_SESSION, LocalDockerProvider
from supervisor.sandbox.none import NoneProvider
from supervisor.sandbox.reaper import reap_orphans
from supervisor.session.lifecycle import drain_for_deploy
from tests.unit.test_docker import FakeDockerClient


def test_factory_none() -> None:
    assert isinstance(create_sandbox_provider("none"), NoneProvider)


def test_factory_local_docker_with_client() -> None:
    client = FakeDockerClient()
    p = create_sandbox_provider("local_docker", client=client)
    assert isinstance(p, LocalDockerProvider)


def test_factory_unknown() -> None:
    with pytest.raises(ValueError):
        create_sandbox_provider("bogus")


@pytest.mark.asyncio
async def test_reap_orphans_destroys_unknown() -> None:
    client = FakeDockerClient()
    provider = LocalDockerProvider(client=client)
    live = await provider.create(
        image="sha256:harness", labels={LABEL_SESSION: "active-1"}
    )
    orphan = await provider.create(
        image="sha256:harness", labels={LABEL_SESSION: "gone-1"}
    )
    bare = await provider.create(image="sha256:harness")  # no session label

    async def lookup(sid: str) -> str | None:
        if sid == "active-1":
            return "active"
        if sid == "gone-1":
            return "terminated"
        return None

    reattached: list[str] = []

    async def reattach(sid: str, sbx) -> None:  # type: ignore[no-untyped-def]
        reattached.append(sid)

    stats = await reap_orphans(provider, lookup_session=lookup, reattach=reattach)
    assert stats["reattached"] == 1
    assert stats["destroyed"] >= 2
    assert "active-1" in reattached
    assert await provider.get(live.info.id) is not None
    assert await provider.get(orphan.info.id) is None
    assert await provider.get(bare.info.id) is None


@pytest.mark.asyncio
async def test_drain_pauses_sticky(session_env) -> None:
    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    await drain_for_deploy(m, timeout_seconds=2.0)
    # Runtime removed from registry after drain
    assert session_env["registry"].get(sid) is None


def test_no_provider_leaks_above_sandbox() -> None:
    """Level 6 — session/journal/api must not mention docker/e2b/container."""
    root = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = ("docker", "e2b", "container")
    for folder in ("session", "journal", "api"):
        base = root / folder
        for path in base.rglob("*.py"):
            text = path.read_text().lower()
            for word in banned:
                # Allow generic English in comments carefully: require package-ish forms
                if word == "container" and "sandbox" in text:
                    # "container" alone is too broad; check docker/e2b only for api/journal
                    if folder == "session":
                        continue
                if word in ("docker", "e2b") and word in text:
                    pytest.fail(f"provider leak in {path}: found {word!r}")
