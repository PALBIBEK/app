"""subagent-contract — events, tree, SubagentOps bridge (R/G/R)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import (
    Envelope,
    JobEnd,
    JobStart,
    SubagentEnd,
    SubagentStart,
)
from supervisor.api.wire import envelope_to_wire
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import decode_event, encode_event
from supervisor.journal.writer import JournalWriter
from supervisor.seams.errors import UnsupportedCapability
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.types import SubagentRequest
from supervisor.session.subagent_ops import SubagentOps
from supervisor.session.tree import (
    build_session_tree,
    unpaired_subagent_starts,
)


def _env(seq: int, payload, *, session_id: str = "parent-1") -> Envelope:
    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_700_000_000_000 + seq,
        turn_id=None,
        trace_id=None,
        payload=payload,
    )


@pytest.fixture
async def journal(tmp_path: Path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (_engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("parent-1", runtime="echo")
        yield w, r


def test_sc_u1_u2_serde_round_trip() -> None:
    start = SubagentStart(
        run_id="run-1",
        child_session_id="c1",
        parent_session_id="p1",
        mode="one_shot",
        provider="memory",
        label="x",
    )
    end = SubagentEnd(run_id="run-1", stop_reason="ok", output="hi")
    js = JobStart(job_id="j1", kind="subagent")
    je = JobEnd(job_id="j1", stop_reason="ok", output=None)
    for ev in (start, end, js, je):
        raw = encode_event(ev)
        back = decode_event(raw)
        assert back == ev


def test_sc_u3_wire_snake_case() -> None:
    env = _env(
        1,
        SubagentStart(
            run_id="r",
            child_session_id="c",
            parent_session_id="p",
            mode="fork",
            provider="memory",
        ),
    )
    wire = envelope_to_wire(env)
    assert wire.type == "subagent_start"
    assert wire.data["child_session_id"] == "c"


def test_sc_u4_session_tree() -> None:
    envs = [
        _env(
            1,
            SubagentStart(
                run_id="r1",
                child_session_id="c1",
                parent_session_id="parent-1",
                mode="one_shot",
                provider="memory",
                label="a",
            ),
        ),
        _env(2, SubagentEnd(run_id="r1", stop_reason="ok", output="out")),
        _env(3, JobStart(job_id="j1", kind="subagent")),
    ]
    tree = build_session_tree("parent-1", envs)
    kids = tree.list_children()
    assert len(kids) == 1
    assert kids[0].id == "c1"
    assert kids[0].activity == "done"
    assert kids[0].provider == "memory"
    assert "j1" in tree.open_job_ids
    assert tree.unpaired_run_ids == frozenset()


def test_sc_u5_unpaired_subagent_start() -> None:
    envs = [
        _env(
            1,
            SubagentStart(
                run_id="orphan",
                child_session_id="c9",
                parent_session_id="parent-1",
                mode="continuable",
                provider="memory",
            ),
        )
    ]
    unpaired = unpaired_subagent_starts(envs)
    assert len(unpaired) == 1
    assert unpaired[0].run_id == "orphan"
    tree = build_session_tree("parent-1", envs)
    assert "orphan" in tree.unpaired_run_ids
    assert tree.list_children()[0].activity == "running"


@pytest.mark.asyncio
async def test_sc_b1_spawn_journals_start_end(journal) -> None:
    w, r = journal
    seams = build_runtime_seams()
    ops = SubagentOps(
        parent_session_id="parent-1",
        transport=seams.subagent_transport,  # type: ignore[arg-type]
        writer=w,
    )
    result = await ops.spawn(
        "memory",
        SubagentRequest(prompt="do the thing", description="task"),
    )
    assert result.status == "ok"
    envs = await r.read("parent-1", since=-1)
    types = [type(e.payload).__name__ for e in envs]
    assert types == ["SubagentStart", "SubagentEnd"]
    assert isinstance(envs[0].payload, SubagentStart)
    assert envs[0].payload.provider == "memory"
    assert envs[0].payload.mode == "one_shot"
    assert isinstance(envs[1].payload, SubagentEnd)
    assert envs[0].payload.run_id == envs[1].payload.run_id
    tree = await r.session_tree("parent-1")
    assert len(tree.list_children()) == 1
    assert tree.list_children()[0].activity == "done"
    assert await r.unpaired_subagent_starts("parent-1") == []


@pytest.mark.asyncio
async def test_sc_b2_background_job_events(journal) -> None:
    w, r = journal
    seams = build_runtime_seams()
    ops = SubagentOps(
        parent_session_id="parent-1",
        transport=seams.subagent_transport,  # type: ignore[arg-type]
        writer=w,
    )
    handle = await ops.start_background(
        "memory",
        SubagentRequest(prompt="bg work"),
        complete_immediately=False,
    )
    assert handle.job_id
    envs = await r.read("parent-1", since=-1)
    assert isinstance(envs[0].payload, JobStart)
    assert isinstance(envs[1].payload, SubagentStart)
    assert envs[1].payload.mode == "background"
    tree = await r.session_tree("parent-1")
    assert handle.job_id in tree.open_job_ids
    assert len(tree.unpaired_run_ids) == 1


@pytest.mark.asyncio
async def test_sc_b3_fail_loud_before_success_end(journal) -> None:
    w, r = journal
    seams = build_runtime_seams()
    ops = SubagentOps(
        parent_session_id="parent-1",
        transport=seams.subagent_transport,  # type: ignore[arg-type]
        writer=w,
    )
    with pytest.raises(UnsupportedCapability):
        await ops.spawn(
            "memory",
            SubagentRequest(prompt="x", persona="hacker"),
        )
    envs = await r.read("parent-1", since=-1)
    assert isinstance(envs[0].payload, SubagentStart)
    assert isinstance(envs[1].payload, SubagentEnd)
    assert envs[1].payload.stop_reason == "error"


def test_sc_c1_contracts_exist() -> None:
    root = Path(__file__).resolve().parents[4]  # agent-platform
    assert (root / "contracts" / "subagent.md").is_file()
    body = (root / "contracts" / "AGENT_CONTRACT.md").read_text()
    assert "subagent_start" in body
    assert "job_start" in body
    assert "spawn_child" in body


def test_sc_a1_session_journal_api_no_provider_concretes() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = (
        "MemorySubagentProvider",
        "FakeModelGatewayClient",
        "HttpModelGatewayClient",
        "NullToolProvider",
        "StaticPromptAssembler",
        "NullContextMemoryProvider",
        "BasicCompactionProvider",
    )
    for pkg in ("journal", "session", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"


def test_sc_a2_mcp_acp_provider_modules_exist() -> None:
    providers = (
        Path(__file__).resolve().parents[2] / "src" / "supervisor" / "seams" / "providers"
    )
    assert (providers / "mcp_tools.py").is_file()
    assert (providers / "acp_subagent.py").is_file()
