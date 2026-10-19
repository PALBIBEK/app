"""Phase 4 — second adapter (SimpleLoop) proves protocol is harness-neutral."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from memory_service.adapters.simple_loop import SimpleLoopAdapter
from memory_service.app import create_app
from memory_service.models import MemorySessionRow
from memory_service.service import MemoryService
from memory_service.tier2_types import NewMemoryItem
from memory_service.types import Scope

SCOPE = {"tenant": "t-loop", "subject": "agent:custom-loop", "namespace": "facts"}


@pytest.fixture
async def app_client():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://memory.test") as client,
    ):
        yield app, client


@pytest.mark.asyncio
async def test_note_is_sync_no_http_until_checkpoint(app_client):
    _app, client = app_client
    adapter = SimpleLoopAdapter(base_url="http://memory.test", scope=SCOPE, client=client)
    await adapter.begin()
    posts = {"n": 0}
    original_post = client.post

    async def counting_post(*args, **kwargs):
        posts["n"] += 1
        return await original_post(*args, **kwargs)

    client.post = counting_post  # type: ignore[method-assign]
    adapter.note("user/message", {"text": "hello from loop"})
    adapter.note("assistant/message", {"text": "ack"})
    assert adapter.pending_count == 2
    assert posts["n"] == 0
    await adapter.checkpoint()
    assert adapter.pending_count == 0
    assert posts["n"] >= 2


@pytest.mark.asyncio
async def test_checkpoint_and_resume_repair(app_client):
    _app, client = app_client
    adapter = SimpleLoopAdapter(base_url="http://memory.test", scope=SCOPE, client=client)
    await adapter.begin()
    adapter.note("assistant/message", {"text": "calling tool"})
    adapter.note("tool/call", {"call_id": "c1", "name": "search", "arguments": {}})
    await adapter.checkpoint()
    loaded = await adapter.resume()
    assert loaded["repaired"] is True
    types = [e["type"] for e in loaded["events"]]
    assert "tool/result" in types
    assert types[-1] == "turn/end"


@pytest.mark.asyncio
async def test_recall_without_yal_bind(app_client):
    _app, client = app_client
    adapter = SimpleLoopAdapter(base_url="http://memory.test", scope=SCOPE, client=client)
    await adapter.begin()
    item_id = await adapter.write_fact("Main project is YAL loop proof")
    result = await adapter.recall("what is my main project", max_tokens=128)
    assert item_id in result["item_ids"]


@pytest.mark.asyncio
async def test_dual_adapters_share_one_service():
    """HTTP loop adapter + in-process MemoryService share one engine."""
    from memory_service.db import init_db, make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    svc = MemoryService(engine=engine)
    await svc.startup()
    scope = Scope(tenant="shared", subject="repo:yal", namespace="facts")
    await svc.create_session(scope, session_id="shared-sess")
    await svc.write(scope, NewMemoryItem(content="Shared fact: deploy uses postgres"))

    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    app.state.memory = svc

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://shared.test") as client,
    ):
        loop = SimpleLoopAdapter(
            base_url="http://shared.test",
            scope={"tenant": "shared", "subject": "repo:yal", "namespace": "facts"},
            client=client,
            session_id="shared-sess",
        )
        await loop.begin()
        hit = await loop.recall("postgres deploy", max_tokens=128, log=False)
        assert any("postgres" in i["content"].lower() for i in hit["items"])

        loop.note("user/message", {"text": "I prefer explicit checkpoints"})
        loop.note("turn/end", {"kind": "ok"})
        await loop.checkpoint()
        loaded = await svc.load("shared-sess")
        assert any(e.type == "user/message" for e in loaded.events)

    await svc.aclose()


def test_schema_has_no_harness_specific_columns():
    names = {c.name for c in MemorySessionRow.__table__.columns}
    banned = {n for n in names if n.startswith(("pi_", "harness_", "cordis_", "langchain_"))}
    assert banned == set()
    required = {"id", "tenant", "subject", "namespace", "format_version", "status", "last_seq"}
    assert required <= names


def test_scope_from_caller_not_yal_file():
    with pytest.raises(ValueError, match="SCOPE_REQUIRED"):
        SimpleLoopAdapter(
            base_url="http://x",
            scope={"tenant": "t", "subject": "", "namespace": "n"},
            client=object(),
        )
