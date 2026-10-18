"""D10 matrix T9–T11 — policy, orphan reaper, local_process destroy hygiene."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, SandboxProfile
from supervisor.sandbox.local_docker import LABEL_SESSION, LocalDockerProvider
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.sandbox.reaper import reap_orphans
from tests.unit.test_catalog_pins import _offer_composition, _sample_tool_set
from tests.unit.test_docker import FakeDockerClient
from tests.unit.test_sandbox_profile import _sample_profile


@pytest.mark.asyncio
async def test_t9_read_only_profile_denies_write_on_docker(session_env, fake_docker: FakeDockerClient) -> None:
    cat = CompositionCatalog()
    prof = cat.offer(
        SandboxProfile(
            name="ro-docker",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            provider="local_docker",
            file_effects="read_only",
            approval_default="ask",
            image_digest="sha256:" + "a" * 64,
        )
    )
    pack = cat.offer(__import__(
        "tests.unit.test_catalog_pins", fromlist=["_sample_prompt_pack"]
    )._sample_prompt_pack())
    tools = cat.offer(_sample_tool_set(tool_names=("echo", "write")))
    comp = cat.offer(
        AgentComposition(
            name="ro-docker-comp",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
        )
    )
    docker_provider = LocalDockerProvider(client=fake_docker)
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch(
        "supervisor.sandbox.resolve.create_sandbox_provider",
        return_value=docker_provider,
    ), patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    denied = await m.invoke_tool(sid, tenant_id="t1", name="write", arguments={"path": "x"})
    assert denied.result.ok is False
    assert denied.result.code == "FILE_EFFECTS_DENIED"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t10_orphan_reaper_reattaches_and_destroys(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    live = await provider.create(
        image="sha256:harness", labels={LABEL_SESSION: "active-1"}
    )
    orphan = await provider.create(
        image="sha256:harness", labels={LABEL_SESSION: "gone-1"}
    )
    bare = await provider.create(image="sha256:harness")

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
async def test_t11_local_process_destroy_removes_workdir(tmp_path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path / "sandboxes")
    sb = await provider.create()
    root = sb.root
    assert root.exists()
    await sb.destroy()
    assert not root.exists()
    assert not await sb.is_alive()
