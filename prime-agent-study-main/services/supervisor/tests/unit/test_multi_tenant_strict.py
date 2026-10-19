"""MULTI_TENANT_STRICT holds for every session, not only for the deployment's default.

Before, a tenant could publish a sandbox profile with a host provider and run
on the host, and a missing e2b dependency quietly fell back to local
processes, per session and at boot.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from supervisor.catalog.storage import artifact_from_dict
from supervisor.config import Settings
from supervisor.sandbox.providers.local_process import LocalProcessProvider
from supervisor.sandbox.resolve import resolve_sandbox_create
from supervisor.sandbox.security.guards import MultiTenantViolation
from tests.support.platform import shipped_harness_catalog

IMAGE = "sha256:" + hashlib.sha256(b"a-real-image").hexdigest()


def _githi():
    return shipped_harness_catalog().resolve_sync("githi-v1")


def _profile(provider: str):
    return artifact_from_dict(
        "sandbox_profile",
        {
            "name": f"p-{provider}",
            "semver": "1.0.0",
            "protocol_version": 1,
            "status": "offered",
            "provider": provider,
            "image_digest": IMAGE,
            "file_effects": "workspace_write",
            "approval_default": "ask",
        },
    )


@pytest.mark.parametrize("provider", ["local_process", "local_docker"])
def test_a_profile_cannot_put_a_strict_tenant_on_the_host(provider: str, tmp_path: Path) -> None:
    with pytest.raises(MultiTenantViolation):
        resolve_sandbox_create(
            _githi(),
            deploy_provider=LocalProcessProvider(base_dir=tmp_path),
            profile=_profile(provider),
            multi_tenant_strict=True,
        )


def test_the_deploy_default_is_checked_too(tmp_path: Path) -> None:
    with pytest.raises(MultiTenantViolation):
        resolve_sandbox_create(
            _githi(),
            deploy_provider=LocalProcessProvider(base_dir=tmp_path),
            multi_tenant_strict=True,
        )


def test_a_missing_isolating_provider_is_an_error_not_a_downgrade(tmp_path: Path) -> None:
    pytest.importorskip("supervisor.sandbox.providers.e2b")
    with pytest.raises(ModuleNotFoundError):  # e2b is not installed here
        resolve_sandbox_create(
            _githi(),
            deploy_provider=LocalProcessProvider(base_dir=tmp_path),
            profile=_profile("e2b"),
            multi_tenant_strict=True,
        )


def test_without_strict_mode_local_sandboxes_still_work(tmp_path: Path) -> None:
    provider, image = resolve_sandbox_create(
        _githi(),
        deploy_provider=LocalProcessProvider(base_dir=tmp_path),
        profile=_profile("local_process"),
        multi_tenant_strict=False,
    )
    assert provider is not None and image == IMAGE


async def test_boot_refuses_to_fall_back_to_local_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from supervisor.bootstrap import bootstrap_supervisor

    monkeypatch.setenv("SANDBOX_PROVIDER", "e2b")  # its dependency is not installed here
    monkeypatch.setenv("MULTI_TENANT_STRICT", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path / "blobs"))
    with pytest.raises(RuntimeError, match="MULTI_TENANT_STRICT forbids"):
        await bootstrap_supervisor(Settings.from_env())
