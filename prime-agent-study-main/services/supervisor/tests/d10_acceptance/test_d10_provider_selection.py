"""D10 matrix T1–T4, T12, T14 — provider selection + digest pins."""

from __future__ import annotations

import pytest

from supervisor.harness import IncompatibleHarness
from supervisor.sandbox.factory import create_sandbox_provider
from supervisor.sandbox.gate import assert_multi_tenant_sandbox_provider
from supervisor.sandbox.local_docker import LocalDockerProvider
from supervisor.sandbox.resolve import assert_sandbox_profile_compatible
from supervisor.catalog.types import SandboxProfile
from tests.support.session_env import make_manifest
from tests.unit.test_docker import FakeDockerClient


def test_t1_factory_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown SANDBOX_PROVIDER"):
        create_sandbox_provider("bogus")


def test_t2_requires_sandbox_with_none_profile_fails() -> None:
    manifest = make_manifest("coding-v1", requires_sandbox=True, image_digest="sha256:" + "a" * 64)
    profile = SandboxProfile(
        name="none-profile",
        semver="1.0.0",
        digest="sha256:x",
        protocol_version=1,
        status="offered",
        provider="none",
        file_effects="workspace_write",
        approval_default="ask",
    )
    with pytest.raises(IncompatibleHarness):
        assert_sandbox_profile_compatible(manifest, profile)


def test_t3_multi_tenant_strict_refuses_local_process() -> None:
    with pytest.raises(ValueError, match="local_process"):
        assert_multi_tenant_sandbox_provider("local_process", multi_tenant_strict=True)


def test_t4_multi_tenant_strict_allows_e2b() -> None:
    assert_multi_tenant_sandbox_provider("e2b", multi_tenant_strict=True) is None


@pytest.mark.asyncio
async def test_t12_create_requires_image_digest(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    with pytest.raises(ValueError, match="requires image digest"):
        await provider.create(image=None)
    sb = await provider.create(image="sha256:" + "c" * 64)
    assert sb.info.image == "sha256:" + "c" * 64


@pytest.mark.asyncio
async def test_t12_docker_create_applies_hardening(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    await provider.create(image="sha256:" + "d" * 64)
    kw = fake_docker.containers.last_run_kwargs
    assert kw.get("mem_limit") == "512m"
    assert kw.get("nano_cpus") == 1_000_000_000
    assert kw.get("cap_drop") == ["ALL"]
    assert kw.get("network_mode") == "none"
    assert kw.get("init") is True


def test_t14_none_provider_kg_harness_no_sandbox() -> None:
    from supervisor.sandbox.none import NoneProvider
    from supervisor.sandbox.resolve import resolve_sandbox_create

    manifest = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
    provider, image = resolve_sandbox_create(
        manifest,
        deploy_provider=NoneProvider(),
        profile=None,
    )
    assert provider is None
    assert image is None
