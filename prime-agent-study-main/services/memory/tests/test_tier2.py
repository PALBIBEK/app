"""Phase 3 Tier 2 semantic bank tests."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app
from memory_service.service import MemoryService
from memory_service.tier2_types import ForgetSelector, NewMemoryItem, TokenBudget
from memory_service.types import Scope


@pytest.fixture
async def svc():
    s = MemoryService(database_url="sqlite+aiosqlite:///:memory:")
    await s.startup()
    yield s
    await s.aclose()


def scope() -> Scope:
    return Scope(tenant="t1", subject="repo:yal", namespace="facts")


@pytest.mark.asyncio
async def test_write_get_provenance(svc: MemoryService):
    item_id = await svc.write(
        scope(),
        NewMemoryItem(
            content="Main project is YAL agent platform",
            confidence=0.9,
            source_session="s1",
            source_seq=3,
        ),
    )
    got = await svc.get(item_id)
    assert got is not None
    assert got.content.startswith("Main project")
    assert got.source_session == "s1"
    assert got.source_seq == 3
    assert got.superseded_by is None


@pytest.mark.asyncio
async def test_supersede_not_update(svc: MemoryService):
    old_id = await svc.write(scope(), NewMemoryItem(content="Favorite color is blue"))
    new_id = await svc.supersede(
        old_id,
        NewMemoryItem(content="Favorite color is green", confidence=0.95),
        scope(),
    )
    old = await svc.get(old_id)
    assert old is not None
    assert old.superseded_by == new_id
    result = await svc.recall(scope(), "favorite color", TokenBudget(max_tokens=200))
    assert new_id in result.item_ids
    assert old_id not in result.item_ids


@pytest.mark.asyncio
async def test_forget_by_id(svc: MemoryService):
    item_id = await svc.write(scope(), NewMemoryItem(content="temp fact about widgets"))
    n = await svc.forget(scope(), ForgetSelector(item_id=item_id))
    assert n == 1
    assert await svc.get(item_id) is None


@pytest.mark.asyncio
async def test_recall_token_budget_packing(svc: MemoryService):
    await svc.write(scope(), NewMemoryItem(content="short A " * 5))
    await svc.write(scope(), NewMemoryItem(content="short B " * 5))
    await svc.write(scope(), NewMemoryItem(content="long " * 200))
    tight = await svc.recall(scope(), "short", TokenBudget(max_tokens=40))
    assert tight.tokens_used <= 40
    assert len(tight.items) >= 1


@pytest.mark.asyncio
async def test_hybrid_identifier_query(svc: MemoryService):
    await svc.write(scope(), NewMemoryItem(content="The API is called billing-v2"))
    await svc.write(scope(), NewMemoryItem(content="I prefer dark mode in the editor"))
    result = await svc.recall(scope(), "billing-v2", TokenBudget(max_tokens=256))
    assert result.items
    assert "billing-v2" in result.items[0].content


@pytest.mark.asyncio
async def test_recall_logs_tier1_memory_recall(svc: MemoryService):
    handle = await svc.create_session(scope())
    item_id = await svc.write(
        scope(), NewMemoryItem(content="Main project is YAL", source_session=handle.id)
    )
    await svc.recall(
        scope(),
        "what is my project",
        TokenBudget(max_tokens=128),
        log_session_id=handle.id,
    )
    loaded = await svc.load(handle.id)
    recalls = [e for e in loaded.events if e.type == "memory/recall"]
    assert len(recalls) == 1
    assert item_id in recalls[0].data["item_ids"]


@pytest.mark.asyncio
async def test_flush_enqueues_extraction_on_turn_end(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "I prefer pytest for all tests"})
    svc.append(handle.id, "turn/end", {"kind": "ok"})
    await svc.flush(handle.id)
    assert await svc.extraction_pending_count() >= 1
    n = await svc.process_extraction_jobs()
    assert n >= 1
    hit = await svc.recall(scope(), "pytest preference", TokenBudget(max_tokens=128))
    assert any("pytest" in i.content.lower() for i in hit.items)


@pytest.mark.asyncio
async def test_extraction_error_does_not_fail_flush(svc: MemoryService):
    class Boom:
        def extract(self, events):
            raise RuntimeError("llm down")

    broken = MemoryService(
        database_url="sqlite+aiosqlite:///:memory:",
        extractor=Boom(),  # type: ignore[arg-type]
    )
    await broken.startup()
    handle = await broken.create_session(scope())
    broken.append(handle.id, "user/message", {"text": "I prefer rust"})
    broken.append(handle.id, "turn/end", {})
    last = await broken.flush(handle.id)
    assert last >= 0
    await broken.process_extraction_jobs()
    await broken.aclose()


@pytest.mark.asyncio
async def test_rebuild_tier2_from_tier1(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(
        handle.id,
        "user/message",
        {"text": "Main project is YAL agent platform"},
    )
    svc.append(handle.id, "turn/end", {})
    await svc.flush(handle.id)
    await svc.process_extraction_jobs()
    before = await svc.recall(scope(), "project YAL", TokenBudget(max_tokens=128))
    assert before.items
    await svc.rebuild_tier2_from_tier1(scope(), handle.id)
    after = await svc.recall(scope(), "project YAL", TokenBudget(max_tokens=128))
    assert after.items
    assert any("YAL" in i.content for i in after.items)


@pytest.mark.asyncio
async def test_held_out_project_fact(svc: MemoryService):
    empty = await svc.recall(scope(), "what is my main project", TokenBudget(max_tokens=64))
    assert empty.items == ()
    await svc.write(
        scope(),
        NewMemoryItem(content="Main project is YAL agent platform", confidence=0.9),
    )
    hit = await svc.recall(scope(), "what is my main project", TokenBudget(max_tokens=64))
    assert hit.items
    assert "YAL" in hit.items[0].content


def test_http_tier2_round_trip():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        r = client.post(
            "/v1/sessions",
            json={"scope": {"tenant": "t", "subject": "s", "namespace": "n"}},
        )
        assert r.status_code == 201
        sid = r.json()["id"]
        scope_body = {"tenant": "t", "subject": "s", "namespace": "n"}
        r = client.post(
            "/v1/memory/items",
            json={
                "scope": scope_body,
                "content": "API is called billing-v2",
                "confidence": 0.9,
            },
        )
        assert r.status_code == 201, r.text
        item_id = r.json()["id"]
        r = client.get(f"/v1/memory/items/{item_id}")
        assert r.status_code == 200
        r = client.post(
            "/v1/memory/recall",
            json={
                "scope": scope_body,
                "query": "billing-v2",
                "max_tokens": 128,
                "log_session_id": sid,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert item_id in body["item_ids"]
        r = client.post(
            f"/v1/memory/items/{item_id}/supersede",
            json={"scope": scope_body, "content": "API is called billing-v3"},
        )
        assert r.status_code == 201
        r = client.post(
            "/v1/memory/forget",
            json={"scope": scope_body, "all_in_scope": True},
        )
        assert r.status_code == 200
        assert r.json()["forgotten"] >= 1
