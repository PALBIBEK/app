"""D6 §5 A–B / T5–T7 — file tools honor TreeIO + composition pin; deny journals."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.agent.factory import create_runtime
from supervisor.agent.runtimes.githi.tools.provider import GithiToolProvider
from supervisor.agent.runtimes.githi.tools.registry import reset_registry_for_tests
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, SandboxProfile
from supervisor.seams.types import ToolInvokeContext
from tests.unit.test_catalog_pins import _sample_prompt_pack


@pytest.fixture(autouse=True)
def _fresh_githi_registry() -> None:
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


@pytest.mark.asyncio
async def test_t5_file_effects_read_only_denies_write_with_journal(
    session_env,
) -> None:
    """T5: file_effects=read_only + write → FILE_EFFECTS_DENIED + ToolStart/ToolEnd pair."""
    cat = CompositionCatalog()
    prof = cat.offer(
        SandboxProfile(
            name="d6-ro",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            provider="local_process",
            file_effects="read_only",
            approval_default="ask",
            image_digest="sha256:" + "c" * 64,
        )
    )
    pack = cat.offer(_sample_prompt_pack(name="d6-pack"))
    # Minimal set that includes write for deny path.
    from tests.unit.test_catalog_pins import _sample_tool_set

    tools = cat.offer(_sample_tool_set(name="d6-ro-tools", tool_names=("echo", "write")))
    comp = cat.offer(
        AgentComposition(
            name="d6-ro-comp",
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

    denied = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="write",
        arguments={"path": "x.txt", "content": "nope"},
        tool_call_id="tc-deny",
    )
    assert denied.result.ok is False
    assert denied.result.code == "FILE_EFFECTS_DENIED"

    events = await session_env["reader"].read(sid, since=-1)
    starts = [e for e in events if isinstance(e.payload, ToolStart)]
    ends = [e for e in events if isinstance(e.payload, ToolEnd)]
    assert len(starts) == 1 and starts[0].payload.tool_name == "write"
    assert len(ends) == 1 and ends[0].payload.is_error is True
    assert ends[0].payload.result.get("code") == "FILE_EFFECTS_DENIED"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t6_githi_without_composition_lists_file_tools(
    supervisor_stack,
) -> None:
    """T6: Githi harness-only → {read, write, ls} list; workspace tools work."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    cr = await client.post(
        "/v1/sessions",
        headers={"X-Tenant-Id": "d6-tenant-a"},
        json={"harness_version": "githi-v1", "runtime": "githi"},
    )
    assert cr.status_code == 201, cr.text
    sid = cr.json()["session_id"]
    runtime = state.manager._registry.get(sid)
    assert runtime is not None and runtime.seams is not None
    assert isinstance(runtime.seams.tool_provider, GithiToolProvider)
    names = {t.name for t in runtime.seams.tool_provider.list_tools()}
    assert {"read", "write", "ls"} <= names

    # TreeIO write/read via provider (workspace tools work).
    ctx = ToolInvokeContext(extra={"session_id": sid})
    w = runtime.seams.tool_provider.invoke(
        ctx, "write", {"file_path": "d6-t6.txt", "content": "t6-ok"}
    )
    assert w.ok is True, w
    r = runtime.seams.tool_provider.invoke(ctx, "read", {"file_path": "d6-t6.txt"})
    assert r.ok is True, r
    assert "t6-ok" in str(r.payload)


@pytest.mark.asyncio
async def test_t7_githi_with_composition_pin_githi_tools_executes(
    supervisor_stack, shipped_catalog, githi_tool_names
) -> None:
    """T7 / §5 B: composition-pinned githi-tools → read/write/ls succeed (not NOT_IMPLEMENTED)."""
    # Offer a githi composition that pins shipped githi-tools (may not exist on disk yet).
    cat = shipped_catalog
    githi_ts = next(
        a for a in cat.list_artifacts("tool_set", status="offered") if a.name == "githi-tools"
    )
    packs = cat.list_artifacts("prompt_pack", status="offered")
    assert packs, "need a prompt_pack to compose"
    pack = packs[0]
    # Unique composition for this acceptance run.
    comp = cat.offer(
        AgentComposition(
            name="d6-githi-tools-v1",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="githi-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=githi_ts.name, digest=githi_ts.digest),
        )
    )
    supervisor_stack["state"].composition_catalog = cat
    supervisor_stack["state"].manager._composition_catalog = cat

    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    cr = await client.post(
        "/v1/sessions",
        headers={"X-Tenant-Id": "d6-tenant-a"},
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "composition_id": comp.name,
        },
    )
    assert cr.status_code == 201, cr.text
    sid = cr.json()["session_id"]
    runtime = state.manager._registry.get(sid)
    assert runtime is not None and runtime.seams is not None
    provider = runtime.seams.tool_provider
    listed = {t.name for t in provider.list_tools()}
    assert set(githi_tool_names) <= listed, (
        f"list_tools {listed} missing pinned githi-tools {githi_tool_names}"
    )

    # HTTP invoke path must execute file tools under composition (D6 north star).
    for name, args in (
        ("write", {"file_path": "pinned.txt", "content": "pinned-d6", "path": "pinned.txt"}),
        ("read", {"file_path": "pinned.txt", "path": "pinned.txt"}),
        ("ls", {"path": "."}),
    ):
        inv = await client.post(
            f"/v1/sessions/{sid}/tools/invoke",
            headers={"X-Tenant-Id": "d6-tenant-a"},
            json={
                "name": name,
                "arguments": args,
                "tool_call_id": f"tc-{name}",
            },
        )
        assert inv.status_code == 200, inv.text
        body = inv.json()
        assert body.get("code") != "TOOL_NOT_IMPLEMENTED", f"{name}: {body}"
        assert body.get("ok") is True, f"{name} failed under composition pin: {body}"
