"""memory-rpc — supervisor HTTP compaction (harness-neutral, R/G/R)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import CompactionEnd, CompactionStart
from supervisor.api.schemas import CompactMemoryRequest
from supervisor.catalog.store import CompositionCatalog
from supervisor.main import create_app
from supervisor.seams.compaction import SUMMARY_MARKER
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.session.manager import MemoryNotConfigured
from tests.support.session_env import make_catalog
from tests.unit.test_catalog_pins import _offer_composition


def test_materialize_writes_memory_rpc_json(tmp_path: Path, monkeypatch) -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.types import MemoryProfile
    from supervisor.harness import materialize_harness_binds

    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://supervisor.test:9090")
    profile = MemoryProfile(
        name="default",
        semver="1.0.0",
        digest="sha256:" + "d" * 64,
        protocol_version=1,
        status="offered",
        summarization_model="cheap-summary",
    )
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    gateway = FakeModelGatewayClient(assistant_text="## Goal\nrpc")
    seams = build_runtime_seams(
        context_memory=BasicCompactionProvider(gateway),
        **{
            k: getattr(bind_runtime_seams(pins, cat), k)
            for k in ("tool_provider", "prompt_assembler", "subagent_transport", "model_gateway")
        },
    )
    yal = materialize_harness_binds(
        tmp_path,
        pins,
        seams,
        memory_profile=profile,
        session_id="sess-mem",
        tenant_id="tenant-1",
    )
    raw = json.loads((yal / "memory_rpc.json").read_text(encoding="utf-8"))
    assert raw["compact_url"] == (
        "http://supervisor.test:9090/v1/sessions/sess-mem/memory/compact"
    )
    assert raw["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_manager_compact_memory_journals_pair(session_env) -> None:
    from supervisor.seams.context_memory import CompactionPreparation

    gateway = FakeModelGatewayClient(assistant_text="## Goal\njournaled")
    m = session_env["manager"]
    m._fallback_seams = build_runtime_seams(
        context_memory=BasicCompactionProvider(gateway),
    )
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    prep = CompactionPreparation(
        first_kept_entry_id="e1",
        messages_to_summarize=[{"role": "user", "content": "history"}],
        tokens_before=90_000,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    outcome = await m.compact_memory(
        sid,
        tenant_id="t1",
        preparation=prep,
        reason="threshold",
    )
    assert outcome.compaction_id.startswith("cmp-")
    assert outcome.stop_reason == "ok"
    assert outcome.summary is not None
    assert SUMMARY_MARKER in outcome.summary
    events = await session_env["reader"].read(sid, since=-1)
    types = [type(e.payload).__name__ for e in events]
    assert types.count("CompactionStart") == 1
    assert types.count("CompactionEnd") == 1
    start = next(e for e in events if isinstance(e.payload, CompactionStart))
    end = next(e for e in events if isinstance(e.payload, CompactionEnd))
    assert start.payload.compaction_id == end.payload.compaction_id == outcome.compaction_id
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_manager_compact_without_memory_raises(session_env) -> None:
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.context_memory import CompactionPreparation

    m = session_env["manager"]
    seams = build_runtime_seams()
    m._fallback_seams = RuntimeSeams(
        tool_provider=seams.tool_provider,
        prompt_assembler=seams.prompt_assembler,
        subagent_transport=seams.subagent_transport,
        model_gateway=seams.model_gateway,
        context_memory=None,
    )
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    prep = CompactionPreparation(
        first_kept_entry_id="e1",
        messages_to_summarize=[],
        tokens_before=0,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    with pytest.raises(MemoryNotConfigured):
        await m.compact_memory(sid, tenant_id="t1", preparation=prep, reason="manual")
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
        gateway = FakeModelGatewayClient(assistant_text="## Goal\nvia-api")
        state.manager._fallback_seams = build_runtime_seams(
            context_memory=BasicCompactionProvider(gateway),
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


@pytest.mark.asyncio
async def test_api_compact_memory_round_trip(api) -> None:
    client, state = api
    headers = {"X-Tenant-Id": "t1"}
    create = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "kg-v1",
            "runtime": "echo",
        },
        headers=headers,
    )
    assert create.status_code == 201
    sid = create.json()["session_id"]
    compact = await client.post(
        f"/v1/sessions/{sid}/memory/compact",
        json=CompactMemoryRequest(
            reason="manual",
            first_kept_entry_id="e0",
            messages_to_summarize=[{"role": "user", "content": "long"}],
            tokens_before=80_000,
            context_window=128_000,
            reserve_tokens=16_384,
        ).model_dump(),
        headers=headers,
    )
    assert compact.status_code == 200
    body = compact.json()
    assert body["compaction_id"].startswith("cmp-")
    assert body["stop_reason"] == "ok"
    assert body["summary"] and SUMMARY_MARKER in body["summary"]
    await state.manager.terminate(sid)
