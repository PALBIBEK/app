"""semantic-recall-plug — recall_for_turn + memory_recall journal (R/G/R)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import MemoryRecall
from supervisor.api.schemas import RecallMemoryRequest
from supervisor.catalog.store import CompositionCatalog
from supervisor.main import create_app
from supervisor.seams.context_memory import MemoryInvokeContext, RecallBlock
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.composite_context_memory import CompositeContextMemoryProvider
from supervisor.seams.providers.markdown_recall import MarkdownRecallProvider
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider
from supervisor.session.memory_ops import MemoryOps, RecallMemoryOutcome
from tests.support.session_env import make_catalog


class _RecallStub:
    def __init__(self, block: RecallBlock | None = None, *, boom: bool = False) -> None:
        self._block = block
        self._boom = boom

    def estimate_tokens(self, messages):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().estimate_tokens(messages)

    def before_compact(self, ctx, preparation, *, reason):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(self, ctx, *, user_prompt: str):  # type: ignore[no-untyped-def]
        if self._boom:
            raise RuntimeError("recall backend down")
        _ = ctx, user_prompt
        return self._block

    def append_plug_state(self, ctx, custom_type, data):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().append_plug_state(ctx, custom_type, data)


@pytest.fixture
async def journal(tmp_path: Path):
    from supervisor.db.engine import open_db
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (_engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("sess-1", runtime="echo")
        yield w, r


@pytest.mark.asyncio
async def test_memory_ops_recall_journals_hit(journal) -> None:
    w, r = journal
    block = RecallBlock(text="Relevant notes\n- project YAL", source="semantic", score=0.9)
    ops = MemoryOps(session_id="sess-1", memory=_RecallStub(block), writer=w)
    outcome = await ops.recall("what is my project?")
    assert isinstance(outcome, RecallMemoryOutcome)
    assert outcome.injected is True
    assert outcome.text and "YAL" in outcome.text
    assert outcome.block_id.startswith("rec-")
    envs = await r.read("sess-1", since=-1)
    assert len(envs) == 1
    assert isinstance(envs[0].payload, MemoryRecall)
    ev = envs[0].payload
    assert ev.block_id == outcome.block_id
    assert ev.injected is True
    assert ev.source == "semantic"
    assert ev.token_estimate > 0


@pytest.mark.asyncio
async def test_memory_ops_recall_miss_journals_not_injected(journal) -> None:
    w, r = journal
    ops = MemoryOps(session_id="sess-1", memory=_RecallStub(None), writer=w)
    outcome = await ops.recall("anything")
    assert outcome.injected is False
    assert outcome.text is None
    ev = (await r.read("sess-1", since=-1))[0].payload
    assert isinstance(ev, MemoryRecall)
    assert ev.injected is False


@pytest.mark.asyncio
async def test_memory_ops_recall_never_raises(journal) -> None:
    w, r = journal
    ops = MemoryOps(session_id="sess-1", memory=_RecallStub(boom=True), writer=w)
    outcome = await ops.recall("query")
    assert outcome.injected is False
    ev = (await r.read("sess-1", since=-1))[0].payload
    assert isinstance(ev, MemoryRecall)
    assert ev.injected is False


def test_markdown_recall_finds_bullet(tmp_path: Path) -> None:
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "# Memory\n\n- Main project is YAL agent platform\n- Favorite color blue\n",
        encoding="utf-8",
    )
    provider = MarkdownRecallProvider(memory_root=mem_dir)
    ctx = MemoryInvokeContext(session_id="s1", workspace_path=str(tmp_path))
    block = provider.recall_for_turn(ctx, user_prompt="what is my main project")
    assert block is not None
    assert "YAL" in block.text
    assert block.source == "semantic"


def test_markdown_recall_degrades_on_missing_store(tmp_path: Path) -> None:
    provider = MarkdownRecallProvider(memory_root=tmp_path / "missing")
    ctx = MemoryInvokeContext(session_id="s1", workspace_path=str(tmp_path))
    assert provider.recall_for_turn(ctx, user_prompt="anything") is None


def test_composite_delegates_recall_to_overlay(tmp_path: Path) -> None:
    from supervisor.seams.context_memory import CompactionPreparation

    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("# Memory\n\n- alpha bravo charlie\n", encoding="utf-8")
    recall = MarkdownRecallProvider(memory_root=mem_dir)
    base = NullContextMemoryProvider()
    composite = CompositeContextMemoryProvider(compaction=base, recall=recall)
    ctx = MemoryInvokeContext(session_id="s1", workspace_path=str(tmp_path))
    block = composite.recall_for_turn(ctx, user_prompt="alpha")
    assert block is not None
    prep = CompactionPreparation(
        first_kept_entry_id="e0",
        messages_to_summarize=[],
        tokens_before=0,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    assert composite.before_compact(ctx, prep, reason="manual").action == "proceed_default"


def test_materialize_writes_recall_url(tmp_path: Path, monkeypatch) -> None:
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.types import MemoryProfile
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams
    from tests.unit.test_catalog_pins import _offer_composition

    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://supervisor.test:9090")
    profile = MemoryProfile(
        name="default",
        semver="1.0.0",
        digest="sha256:" + "d" * 64,
        protocol_version=1,
        status="offered",
        recall_enabled=True,
        recall_backend="githi_markdown",
    )
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = build_runtime_seams()
    yal = materialize_harness_binds(
        tmp_path,
        pins,
        seams,
        memory_profile=profile,
        session_id="sess-rec",
        tenant_id="tenant-1",
    )
    raw = json.loads((yal / "memory_rpc.json").read_text(encoding="utf-8"))
    assert raw["recall_url"] == (
        "http://supervisor.test:9090/v1/sessions/sess-rec/memory/recall"
    )


def test_mcp_semantic_recall_degrades_on_error() -> None:
    from supervisor.seams.providers.mcp_semantic_recall import McpSemanticRecallProvider

    provider = McpSemanticRecallProvider(server_url="http://127.0.0.1:1")
    ctx = MemoryInvokeContext(session_id="s1")
    assert provider.recall_for_turn(ctx, user_prompt="hello") is None


def test_srp_leak_no_recall_concretes_in_session_journal_api() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = (
        "MarkdownRecallProvider",
        "McpSemanticRecallProvider",
        "CompositeContextMemoryProvider",
    )
    for pkg in ("journal", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"
    session_root = src / "session"
    for path in session_root.rglob("*.py"):
        if path.name == "memory_ops.py":
            continue
        text = path.read_text()
        for needle in banned:
            assert needle not in text, f"{path}: {needle}"


@pytest.mark.asyncio
async def test_manager_recall_memory_journals(session_env) -> None:
    from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
    from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient

    class _HitRecall(NullContextMemoryProvider):
        def recall_for_turn(self, ctx, *, user_prompt: str):  # type: ignore[no-untyped-def]
            _ = ctx, user_prompt
            return RecallBlock(text="note", source="semantic")

    gateway = FakeModelGatewayClient()
    recall = _HitRecall()
    compaction = BasicCompactionProvider(gateway)
    composite = CompositeContextMemoryProvider(compaction=compaction, recall=recall)
    m = session_env["manager"]
    m._fallback_seams = build_runtime_seams(context_memory=composite)
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    outcome = await m.recall_memory(sid, tenant_id="t1", user_prompt="hello")
    assert outcome.injected is True
    events = await session_env["reader"].read(sid, since=-1)
    assert any(isinstance(e.payload, MemoryRecall) for e in events)
    await m.terminate(sid)


@pytest.fixture
async def api(tmp_path):
    from supervisor.app import build_supervisor
    from supervisor.db.engine import open_db

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=32,
        )

        class _HitRecall(NullContextMemoryProvider):
            def recall_for_turn(self, ctx, *, user_prompt: str):  # type: ignore[no-untyped-def]
                _ = ctx, user_prompt
                return RecallBlock(text="via-api recall", source="semantic")

        state.manager._fallback_seams = build_runtime_seams(context_memory=_HitRecall())
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


@pytest.mark.asyncio
async def test_api_recall_round_trip(api) -> None:
    client, state = api
    headers = {"X-Tenant-Id": "t1"}
    create = await client.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers=headers,
    )
    assert create.status_code == 201
    sid = create.json()["session_id"]
    recall = await client.post(
        f"/v1/sessions/{sid}/memory/recall",
        json=RecallMemoryRequest(user_prompt="hello").model_dump(),
        headers=headers,
    )
    assert recall.status_code == 200
    body = recall.json()
    assert body["injected"] is True
    assert "via-api" in body["text"]
    await state.manager.terminate(sid)
