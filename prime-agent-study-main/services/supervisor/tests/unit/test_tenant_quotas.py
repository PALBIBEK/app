"""Phase 2 — multi-tenant quotas, rate limits, sandbox gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.db.engine import open_db
from supervisor.policy.quotas import QuotaExceeded, TenantQuotaEnforcer, TenantQuotaLimits
from supervisor.policy.rate_limit import TenantRateLimiter
from supervisor.sandbox.gate import assert_multi_tenant_sandbox_provider
from tests.support.session_env import make_catalog


@pytest.mark.asyncio
async def test_quota_blocks_tenant_session_cap(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'q.db'}") as (_, factory):
        from supervisor.journal.reader import JournalReader
        from supervisor.journal.writer import JournalWriter
        from supervisor.session.manager import SessionManager
        from supervisor.session.registry import SessionRegistry

        quotas = TenantQuotaEnforcer(
            factory,
            limits=TenantQuotaLimits(max_sessions_per_tenant=1),
        )
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        m = SessionManager(
            session_factory=factory,
            writer=writer,
            reader=reader,
            registry=SessionRegistry(),
            catalog=make_catalog(),
            sandbox_provider=None,
            quotas=quotas,
        )
        await m.create(tenant_id="tenant-a", harness_version="kg-v1", runtime_name="echo")
        with pytest.raises(QuotaExceeded, match="max_sessions_per_tenant"):
            await m.create(tenant_id="tenant-a", harness_version="kg-v1", runtime_name="echo")


@pytest.mark.asyncio
async def test_quota_allows_other_tenant(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'q.db'}") as (_, factory):
        from supervisor.journal.reader import JournalReader
        from supervisor.journal.writer import JournalWriter
        from supervisor.session.manager import SessionManager
        from supervisor.session.registry import SessionRegistry

        quotas = TenantQuotaEnforcer(
            factory,
            limits=TenantQuotaLimits(max_sessions_per_tenant=1),
        )
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        m = SessionManager(
            session_factory=factory,
            writer=writer,
            reader=reader,
            registry=SessionRegistry(),
            catalog=make_catalog(),
            sandbox_provider=None,
            quotas=quotas,
        )
        await m.create(tenant_id="tenant-a", harness_version="kg-v1", runtime_name="echo")
        sid_b = await m.create(tenant_id="tenant-b", harness_version="kg-v1", runtime_name="echo")
        assert sid_b


@pytest.mark.asyncio
async def test_quota_blocks_sandbox_cap(session_env) -> None:
    quotas = TenantQuotaEnforcer(
        session_env["factory"],
        limits=TenantQuotaLimits(max_sandboxes_per_tenant=1),
    )
    m = session_env["manager"]
    m._quotas = quotas  # noqa: SLF001
    await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")
    with pytest.raises(QuotaExceeded, match="max_sandboxes_per_tenant"):
        await m.create(tenant_id="t1", harness_version="coding-v1", runtime_name="echo")


def test_multi_tenant_gate_refuses_local_docker() -> None:
    with pytest.raises(ValueError, match="local_docker"):
        assert_multi_tenant_sandbox_provider("local_docker", multi_tenant_strict=True)


def test_multi_tenant_gate_allows_e2b() -> None:
    assert_multi_tenant_sandbox_provider("e2b", multi_tenant_strict=True)


def test_rate_limiter_blocks_burst() -> None:
    limiter = TenantRateLimiter(max_per_minute=2)
    assert limiter.allow("t1")
    assert limiter.allow("t1")
    assert not limiter.allow("t1")


@pytest.mark.asyncio
async def test_metrics_snapshot_counts_tenants(session_env) -> None:
    from supervisor.obs.metrics import MetricsRegistry

    m = session_env["manager"]
    metrics = MetricsRegistry()
    await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    snap = await metrics.snapshot(m)
    assert snap["tenants"]["t1"]["active_sessions"] == 1
