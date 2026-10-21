"""Agent commands never run on the host in production or staging unless an operator says so.

Found by running the stack: on the host-process provider the agent's shell read the
supervisor's secrets through /proc, found another tenant's files and wrote to the
host. That provider is for development; production refused it only under the
opt-in MULTI_TENANT_STRICT, and silently fell back to it when Docker was missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.sandbox.security.guards import (
    HostSandboxRefused,
    assert_host_sandbox_allowed,
    host_sandbox_allowed,
)
from supervisor.temporal.domain import domain_kind


@pytest.mark.parametrize("env", ["production", "staging"])
def test_production_and_staging_refuse_host_sandboxes(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("ALLOW_HOST_SANDBOX", raising=False)
    assert not host_sandbox_allowed()
    with pytest.raises(HostSandboxRefused) as refused:
        assert_host_sandbox_allowed("local_process")
    assert domain_kind(refused.value) == "HostSandboxRefused", "a final answer, not retried"
    for isolated in ("local_docker", "e2b", "none"):
        assert_host_sandbox_allowed(isolated)


def test_an_operator_can_opt_in_and_development_allows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_HOST_SANDBOX", "1")
    assert_host_sandbox_allowed("local_process")
    monkeypatch.delenv("ALLOW_HOST_SANDBOX")
    monkeypatch.setenv("ENV", "development")
    assert_host_sandbox_allowed("local_process")


async def test_production_never_falls_back_to_the_host_when_the_sandbox_library_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without MULTI_TENANT_STRICT, boot used to log a warning and run every tenant's
    commands on the host; production now refuses to start instead."""
    from supervisor.bootstrap import bootstrap_supervisor
    from supervisor.config import Settings

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_HOST_SANDBOX", raising=False)
    monkeypatch.delenv("MULTI_TENANT_STRICT", raising=False)
    monkeypatch.setenv("SANDBOX_PROVIDER", "e2b")  # its dependency is not installed here
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-service-jwt-secret-32-bytes!!")
    for name, value in {"OBJECT_STORAGE": "s3", "OBJECT_STORAGE_ENDPOINT": "http://127.0.0.1:9",
                        "OBJECT_STORAGE_BUCKET": "boot-test", "WORKSPACE_STORE": "postgres"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path / "blobs"))
    with pytest.raises(RuntimeError, match="never fall back to running agent commands on the host"):
        await bootstrap_supervisor(Settings.from_env())


async def test_production_refuses_a_configured_host_sandbox_at_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from supervisor.bootstrap import bootstrap_supervisor
    from supervisor.config import Settings

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_HOST_SANDBOX", raising=False)
    monkeypatch.setenv("SANDBOX_PROVIDER", "local_process")
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-service-jwt-secret-32-bytes!!")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path / "blobs"))
    with pytest.raises(HostSandboxRefused):
        await bootstrap_supervisor(Settings.from_env())
