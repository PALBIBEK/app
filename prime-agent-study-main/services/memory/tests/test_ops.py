"""Phase 5 — ops: retention, erase audit, tenant gate, metrics, cost."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app
from memory_service.cost import CachingEmbedder, flush_batch_is_barren
from memory_service.metrics import METRICS
from memory_service.service import MemoryService
from memory_service.tenant import TenantRequired, require_tenant
from memory_service.tier2_types import ForgetSelector, NewMemoryItem, TokenBudget
from memory_service.types import Scope, SessionEvent


@pytest.fixture
async def svc(tmp_path: Path):
    for name in (
        "recall_injections",
        "recall_items_injected",
        "extraction_jobs_skipped_barren",
        "bank_writes",
        "bank_supersessions",
        "archive_sessions",
        "embed_cache_hits",
        "embed_cache_misses",
        "resume_ok",
    ):
        setattr(METRICS, name, 0)
    s = MemoryService(
        database_url="sqlite+aiosqlite:///:memory:",
        archive_dir=str(tmp_path / "archive"),
    )
    await s.startup()
    yield s
    await s.aclose()


def scope(tenant: str = "t1") -> Scope:
    return Scope(tenant=tenant, subject="repo:yal", namespace="facts")


@pytest.mark.asyncio
async def test_archive_then_load(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "keep me forever"})
    await svc.flush(handle.id)
    info = await svc.archive_session(handle.id)
    assert info["archived_events"] == 1
    loaded = await svc.load(handle.id)
    assert loaded.status == "archived"
    assert loaded.events[0].data["text"] == "keep me forever"


@pytest.mark.asyncio
async def test_forget_writes_audit(svc: MemoryService):
    item_id = await svc.write(scope(), NewMemoryItem(content="secret preference xyz"))
    n = await svc.forget(
        scope(),
        ForgetSelector(item_id=item_id),
        actor="admin@yal",
        reason="user request",
    )
    assert n == 1
    audit = await svc._erase.list_audit("t1")
    assert audit
    assert audit[0]["actor"] == "admin@yal"
    assert audit[0]["reason"] == "user request"


@pytest.mark.asyncio
async def test_erase_subject_scoped(svc: MemoryService):
    await svc.write(scope("t1"), NewMemoryItem(content="yal fact"))
    other = Scope(tenant="t1", subject="repo:other", namespace="facts")
    await svc.write(other, NewMemoryItem(content="other fact"))
    await svc.create_session(scope("t1"), session_id="s-yal")
    result = await svc.erase_subject(
        tenant="t1", subject="repo:yal", actor="dpo", reason="GDPR"
    )
    assert result["items_forgotten"] == 1
    assert result["sessions_marked"] == 1
    hit = await svc.recall(other, "other fact", TokenBudget(max_tokens=64))
    assert hit.items
    empty = await svc.recall(scope("t1"), "yal", TokenBudget(max_tokens=64))
    assert empty.items == ()


def test_tenant_gate_rejects_empty():
    with pytest.raises(TenantRequired):
        require_tenant("")
    with pytest.raises(TenantRequired):
        require_tenant(None)


@pytest.mark.asyncio
async def test_cross_tenant_isolation(svc: MemoryService):
    await svc.write(scope("tenant-a"), NewMemoryItem(content="alpha only secret"))
    hit_b = await svc.recall(scope("tenant-b"), "alpha only secret", TokenBudget(max_tokens=64))
    assert hit_b.items == ()


@pytest.mark.asyncio
async def test_metrics_four_signals(svc: MemoryService):
    handle = await svc.create_session(scope())
    await svc.write(scope(), NewMemoryItem(content="Main project is YAL"))
    await svc.recall(scope(), "project", TokenBudget(max_tokens=64), log_session_id=handle.id)
    await svc.report_recall_used(1)
    snap = await svc.metrics_snapshot()
    assert "recall_precision" in snap
    assert "extraction_yield" in snap
    assert "bank_growth_vs_supersession" in snap
    assert "resume_success_rate" in snap
    assert snap["bank_writes"] >= 1
    assert snap["recall_injections"] >= 1


def test_embed_cache_hit():
    emb = CachingEmbedder()
    a = emb.embed("same text content")
    hits_before = METRICS.embed_cache_hits
    b = emb.embed("same text content")
    assert a == b
    assert METRICS.embed_cache_hits >= hits_before + 1


def test_barren_flush_detection():
    barren = [
        SessionEvent(type="tool/result", seq=0, time=1, data={"ok": True}),
        SessionEvent(type="turn/end", seq=1, time=2, data={}),
    ]
    assert flush_batch_is_barren(barren) is True
    rich = [
        SessionEvent(
            type="user/message", seq=0, time=1, data={"text": "I prefer pytest always"}
        ),
        SessionEvent(type="turn/end", seq=1, time=2, data={}),
    ]
    assert flush_batch_is_barren(rich) is False


@pytest.mark.asyncio
async def test_barren_flush_skips_extraction(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "tool/result", {"call_id": "c", "status": "ok"})
    svc.append(handle.id, "turn/end", {})
    await svc.flush(handle.id)
    assert await svc.extraction_pending_count() == 0
    assert METRICS.extraction_jobs_skipped_barren >= 1


def test_rls_sql_and_slo_docs_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "rls_postgres.sql").is_file()
    assert (root / "docs" / "slo.md").is_file()


def test_http_metrics_and_erase():
    with tempfile.TemporaryDirectory():
        app = create_app(database_url="sqlite+aiosqlite:///:memory:")
        with TestClient(app) as client:
            r = client.get("/v1/metrics")
            assert r.status_code == 200
            assert "recall_precision" in r.json()
            r = client.post(
                "/v1/memory/items",
                json={
                    "scope": {"tenant": "t", "subject": "s", "namespace": "n"},
                    "content": "to erase",
                },
            )
            assert r.status_code == 201
            r = client.post(
                "/v1/memory/erase_subject",
                json={
                    "tenant": "t",
                    "subject": "s",
                    "actor": "dpo",
                    "reason": "test",
                },
            )
            assert r.status_code == 200
            assert r.json()["items_forgotten"] >= 1
