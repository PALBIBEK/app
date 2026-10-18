"""Phase 2 step 6 — sandbox profile policy wiring."""

from __future__ import annotations

from dataclasses import replace

import pytest

from supervisor.agent.events import ApprovalRequired
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, SandboxProfile
from supervisor.policy.sandbox import SandboxPolicy
from tests.unit.test_catalog_pins import _offer_composition
from tests.unit.test_sandbox_profile import _sample_profile


def test_read_only_blocks_write_tool() -> None:
    policy = SandboxPolicy()
    policy.configure("s1", replace(_sample_profile(), file_effects="read_only"))
    assert policy.check_tool_invoke("s1", "write") is not None
    assert policy.check_tool_invoke("s1", "echo") is None


def test_workspace_write_blocks_bash() -> None:
    policy = SandboxPolicy()
    prof = SandboxProfile(
        name="ws",
        semver="1.0.0",
        digest="sha256:x",
        protocol_version=1,
        status="offered",
        provider="none",
        file_effects="workspace_write",
        approval_default="ask",
    )
    policy.configure("s1", prof)
    assert policy.check_tool_invoke("s1", "bash") is not None
    assert policy.check_tool_invoke("s1", "write") is None


def test_danger_full_access_allows_bash() -> None:
    policy = SandboxPolicy()
    prof = replace(_sample_profile(), file_effects="danger_full_access")
    policy.configure("s1", prof)
    assert policy.check_tool_invoke("s1", "bash") is None


def test_approval_never_auto_choice() -> None:
    policy = SandboxPolicy()
    prof = replace(_sample_profile(), approval_default="never")
    policy.configure("s1", prof)
    assert policy.auto_approve_choice("s1", ["yes", "no"]) == "yes"
    assert policy.auto_approve_choice("s2", ["yes", "no"]) is None


@pytest.mark.asyncio
async def test_invoke_tool_denied_by_read_only_profile(session_env) -> None:
    from unittest.mock import patch

    from supervisor.agent.factory import create_runtime
    from supervisor.catalog.types import AgentComposition, DigestRef
    from tests.unit.test_catalog_pins import _sample_tool_set

    cat = CompositionCatalog()
    prof = cat.offer(
        SandboxProfile(
            name="ro-profile",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            provider="local_process",
            file_effects="read_only",
            approval_default="ask",
            image_digest="sha256:img",
        )
    )
    pack = cat.offer(__import__(
        "tests.unit.test_catalog_pins", fromlist=["_sample_prompt_pack"]
    )._sample_prompt_pack())
    tools = cat.offer(_sample_tool_set(tool_names=("echo", "write")))
    comp = cat.offer(
        AgentComposition(
            name="ro-comp",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
        )
    )
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch(
        "supervisor.sandbox.resolve.create_sandbox_provider",
        return_value=session_env["provider"],
    ), patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    denied = await m.invoke_tool(sid, tenant_id="t1", name="write", arguments={"path": "x"})
    assert denied.result.ok is False
    assert denied.result.code == "FILE_EFFECTS_DENIED"
    allowed = await m.invoke_tool(sid, tenant_id="t1", name="echo", arguments={"text": "ok"})
    assert allowed.result.ok is True
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_approval_never_skips_awaiting_status(tmp_path) -> None:
    from supervisor.db.engine import open_db
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.policy.approvals import ApprovalBroker
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry
    from tests.support.session_env import make_catalog

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}") as (_, factory):
        catalog = make_catalog()
        cat = CompositionCatalog()
        prof = cat.offer(replace(_sample_profile(), approval_default="never"))
        comp, pack, tools = _offer_composition(cat)
        comp2 = cat.offer(
            AgentComposition(
                name="never-approve",
                semver="1.0.0",
                digest="",
                protocol_version=1,
                status="offered",
                harness_version="kg-v1",
                prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
                tool_set=DigestRef(id=tools.name, digest=tools.digest),
                sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
            )
        )
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "b"),
            reader=JournalReader(factory, blob_dir=tmp_path / "b"),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory),
            composition_catalog=cat,
        )
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp2.name,
        )
        await m._handle_approval_required(
            sid,
            "turn-1",
            ApprovalRequired(
                approval_id="",
                prompt="run bash?",
                options=["yes", "no"],
                timeout_s=60,
            ),
        )
    row = await m._get_session_row(sid)
    assert row.status == "active"
    payloads = await m.event_payloads(sid)
    assert "ApprovalResolved" in payloads
    await m.terminate(sid)
