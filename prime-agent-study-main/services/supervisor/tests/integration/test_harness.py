"""Harness versioning integration — pin, deprecation, compat, policy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from supervisor.agent.events import ToolStart
from supervisor.db.engine import open_db
from supervisor.harness import (
    HarnessCatalog,
    IncompatibleHarness,
    RecoveryAction,
    RecoveryPolicy,
    ToolClass,
    default_recovery_policy,
)
from supervisor.session.manager import SessionManager
from supervisor.session.recovery import Recovery, ResumeState
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import TrackingProvider, make_manifest


def _policy(*, non_atomic: RecoveryAction) -> RecoveryPolicy:
    base = default_recovery_policy()
    return RecoveryPolicy(
        default=base.default,
        by_tool_class={**base.by_tool_class, ToolClass.NON_ATOMIC: non_atomic},
        tool_classes=dict(base.tool_classes),
    )






@pytest.mark.asyncio
async def test_compat_gate_before_sandbox(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        from supervisor.journal.reader import JournalReader
        from supervisor.journal.writer import JournalWriter

        bad = replace(
            make_manifest(
                "coding-v1",
                requires_sandbox=True,
                image_digest="sha256:" + "a" * 64,
            ),
            protocol_version=99,
        )
        catalog = HarnessCatalog({"coding-v1": bad})
        provider = TrackingProvider()
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            sandbox_provider=provider,
        )
        with pytest.raises(IncompatibleHarness, match="protocol_version=99"):
            await m.create(tenant_id="t", harness_version="coding-v1")
        assert provider.create_calls == []


@pytest.mark.asyncio
async def test_recovery_policy_pinned(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        from supervisor.journal.reader import JournalReader
        from supervisor.journal.writer import JournalWriter

        v1 = make_manifest(
            "kg-v1",
            requires_sandbox=False,
            image_digest=None,
            recovery=_policy(non_atomic=RecoveryAction.FAIL),
        )
        catalog = HarnessCatalog({"kg-v1": v1})
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        registry = SessionRegistry()
        m = SessionManager(
            session_factory=factory,
            writer=writer,
            reader=reader,
            registry=registry,
            catalog=catalog,
            sandbox_provider=None,
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        await m._stop_runtime_keep_session(sid)
        await writer.append(
            sid,
            ToolStart(tool_call_id="tc1", tool_name="bash", args={}),
            turn_id="t",
            trace_id=None,
        )

        # Publish v2 that would auto_resume non_atomic — must not affect v1 session.
        v2 = make_manifest(
            "kg-v2",
            requires_sandbox=False,
            image_digest=None,
            recovery=_policy(non_atomic=RecoveryAction.AUTO_RESUME),
        )
        catalog.register(v2)
        catalog.register(
            make_manifest(
                "kg-v1",
                requires_sandbox=False,
                image_digest=None,
                recovery=_policy(non_atomic=RecoveryAction.AUTO_RESUME),
            )
        )

        row = await m._get_session_row(sid)
        recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
        decision = await recovery.decide(sid, session_row=row)
        assert decision.state == ResumeState.FAILED
        assert decision.harness is not None
        assert decision.harness.harness_version == "kg-v1"
        await m.terminate(sid)


def test_harness_leak_checks() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "supervisor" / "harness"
    leaks_rt: list[str] = []
    for path in root.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                leaks_rt.append(f"{path.name}:{i}:{line.strip()}")
    assert leaks_rt == [], leaks_rt
    resolver = (root / "services" / "resolver.py").read_text()
    assert "def latest" not in resolver
    assert "_reject_latest" in resolver
