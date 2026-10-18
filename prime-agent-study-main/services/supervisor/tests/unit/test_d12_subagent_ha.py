"""D12 production gaps — lab_echo quarantine, depth, recovery, HA signals."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import SubagentStart
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.seams.errors import DepthExceeded, UnsupportedCapability
from supervisor.seams.factory import build_runtime_seams, lab_echo_allowed
from supervisor.seams.providers.memory_subagent import LAB_ECHO_PROVIDER
from supervisor.seams.types import SubagentRequest
from supervisor.session.recovery import Recovery, ResumeState
from supervisor.session.registry import SessionRegistry
from supervisor.session.subagent_ops import SubagentOps
from tests.support.session_env import make_catalog, make_manifest


def _env(seq: int, payload, *, session_id: str = "s1"):
    from supervisor.agent.events import Envelope

    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_700_000_000_000 + seq,
        turn_id=None,
        trace_id=None,
        payload=payload,
    )


def test_d12_lab_echo_quarantine_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ALLOW_LAB_ECHO", raising=False)
    monkeypatch.delenv("ACP_BASE_URL", raising=False)
    assert lab_echo_allowed() is True
    seams = build_runtime_seams()
    names = seams.subagent_transport._registry.names()  # type: ignore[attr-defined]
    assert LAB_ECHO_PROVIDER in names
    assert "memory" in names  # deprecated alias for tests
    assert seams.subagent_transport.default_provider == LAB_ECHO_PROVIDER  # type: ignore[attr-defined]


def test_d12_staging_no_lab_echo_uses_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.delenv("ALLOW_LAB_ECHO", raising=False)
    monkeypatch.delenv("ACP_BASE_URL", raising=False)
    assert lab_echo_allowed() is False
    seams = build_runtime_seams()
    names = seams.subagent_transport._registry.names()  # type: ignore[attr-defined]
    assert LAB_ECHO_PROVIDER not in names
    assert "memory" not in names
    assert "nested_session" in names
    assert seams.subagent_transport.default_provider == "nested_session"  # type: ignore[attr-defined]


def test_d12_acp_preferred_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("ACP_BASE_URL", "http://acp.test:8091")
    seams = build_runtime_seams()
    assert seams.subagent_transport.default_provider == "acp"  # type: ignore[attr-defined]
    assert "acp" in seams.subagent_transport._registry.names()  # type: ignore[attr-defined]


def test_d12_continuable_fail_loud_on_lab() -> None:
    seams = build_runtime_seams()
    transport = seams.subagent_transport
    with pytest.raises(UnsupportedCapability):
        transport.start_continuable(  # type: ignore[union-attr]
            LAB_ECHO_PROVIDER, SubagentRequest(prompt="nope")
        )


def test_d12_http_schema_default_not_memory() -> None:
    from supervisor.api.schemas import SubagentCommandRequest

    body = SubagentCommandRequest(prompt="x")
    assert body.provider == ""


@pytest.mark.asyncio
async def test_d12_unpaired_subagent_needs_review(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo")
        await w.append(
            "s1",
            SubagentStart(
                run_id="orphan-run",
                child_session_id="c1",
                parent_session_id="s1",
                mode="one_shot",
                provider="lab_echo",
            ),
            turn_id=None,
            trace_id=None,
        )
        catalog = make_catalog()
        recovery = Recovery(registry=SessionRegistry(), reader=r, catalog=catalog)
        row = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
        # minimal session row view
        class _Row:
            harness_version = "kg-v1"
            runtime = "echo"
            status = "active"
            manifest = None

        decision = await recovery.decide("s1", session_row=_Row())
        assert decision.state == ResumeState.NEEDS_REVIEW
        assert "orphan-run" in decision.unpaired_subagent_run_ids


@pytest.mark.asyncio
async def test_d12_depth_enforced_from_ops(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("parent-1", runtime="echo")
        seams = build_runtime_seams()
        ops = SubagentOps(
            parent_session_id="parent-1",
            transport=seams.subagent_transport,  # type: ignore[arg-type]
            writer=w,
            max_depth=3,
        )
        with pytest.raises(DepthExceeded) as ei:
            await ops.spawn(
                LAB_ECHO_PROVIDER,
                SubagentRequest(prompt="x", parent_depth=3),
            )
        assert ei.value.code == "DEPTH_EXCEEDED"


def test_d12_contracts_document_lab_echo() -> None:
    root = Path(__file__).resolve().parents[4]
    body = (root / "contracts" / "subagent.md").read_text()
    assert "lab_echo" in body
    assert "DEPTH_EXCEEDED" in body
    assert "job_list" in body and "deferred" in body.lower()


def test_d12_staging_compose_ha_policy() -> None:
    root = Path(__file__).resolve().parents[4]
    text = (root / "deploy" / "staging" / "docker-compose.staging.yml").read_text()
    assert "cold-resume-only" in text
    assert "ALLOW_LAB_ECHO" in text
    assert "owner_required" in text or "advisory" in text.lower()
