"""Agent-core-runtime seam — factory, capabilities, RuntimeSeams (QUEUE step 0)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from supervisor.agent.capabilities import Capabilities
from supervisor.agent.factory import create_runtime, known_runtime_names
from supervisor.agent.seams import RuntimeSeams
from supervisor.harness import load_supported_protocol_versions
from supervisor.harness.services.compat_checker import capabilities_for
from tests.support.replay import ReplaySandbox


# --- U3 / U9 / N2: factory loud fail + known names ---


def test_u9_known_runtime_names() -> None:
    assert known_runtime_names() == {"echo", "harness"}


def test_u3_n2_unknown_runtime_fails_loud() -> None:
    with pytest.raises(ValueError, match="unknown runtime_name=.nope."):
        create_runtime("nope")


# --- U1 / U4: echo get_capabilities on Protocol surface ---


@pytest.mark.asyncio
async def test_u1_echo_satisfies_agent_runtime_including_get_capabilities() -> None:
    rt = create_runtime("echo")
    for name in (
        "start",
        "send",
        "abort",
        "get_state",
        "get_history",
        "events",
        "stop",
        "get_capabilities",
    ):
        assert hasattr(rt, name), f"missing {name}"

    await rt.start(None, None)
    await rt.send("hi", command_id="u1")
    state = await rt.get_state()
    assert state.session_id
    caps = await rt.get_capabilities()
    assert isinstance(caps, Capabilities)
    await rt.stop()


@pytest.mark.asyncio
async def test_u4_echo_get_capabilities_shape() -> None:
    rt = create_runtime("echo")
    caps = await rt.get_capabilities()
    supported = load_supported_protocol_versions()
    assert int(caps.protocol_version) in supported
    assert caps.supports_abort is True
    assert caps.supports_get_state is True


# --- U6 / U7 / A3: RuntimeSeams injection ---


def test_u6_echo_accepts_empty_seams() -> None:
    seams = RuntimeSeams()
    assert seams.tool_provider is None
    assert seams.prompt_assembler is None
    assert seams.subagent_transport is None
    assert seams.model_gateway is None
    rt = create_runtime("echo", seams=seams)
    assert rt.seams is seams


@pytest.mark.asyncio
async def test_u7_echo_stores_tool_provider_handle_without_invoke() -> None:
    handle = object()
    seams = RuntimeSeams(tool_provider=handle)
    rt = create_runtime("echo", seams=seams)
    assert rt.seams is not None
    assert rt.seams.tool_provider is handle
    await rt.start(None, None)
    await rt.send("still-works", command_id="u7")
    state = await rt.get_state()
    assert state.session_id
    await rt.stop()


def test_a3_seams_are_per_runtime_not_global() -> None:
    a = RuntimeSeams(tool_provider=object())
    b = RuntimeSeams(tool_provider=object())
    rt_a = create_runtime("echo", seams=a)
    rt_b = create_runtime("echo", seams=b)
    assert rt_a.seams is a
    assert rt_b.seams is b
    assert rt_a.seams is not rt_b.seams
    assert rt_a.seams.tool_provider is not rt_b.seams.tool_provider


# --- U2 / U5 / U8 / N3 / N4: harness capabilities, model, sandbox, mode ---


@pytest.mark.asyncio
async def test_u5_harness_get_capabilities_matches_compat() -> None:
    rt = create_runtime("harness")
    caps = await rt.get_capabilities()
    static = capabilities_for("harness")
    assert caps.supports_fork == static.supports_fork
    assert caps.supports_approvals == static.supports_approvals
    assert int(caps.protocol_version) in static.protocol_versions
    assert caps.supports_get_state == ("get_state" in static.commands)


@pytest.mark.asyncio
async def test_u2_harness_stores_injected_model_provider_in_spawn_argv() -> None:
    class RecordingSandbox(ReplaySandbox):
        def __init__(self) -> None:
            super().__init__()
            self.spawn_cmds: list[Any] = []

        async def spawn(self, cmd, *, cwd=None, env=None):  # type: ignore[no-untyped-def]
            self.spawn_cmds.append(list(cmd))
            return await super().spawn(cmd, cwd=cwd, env=env)

    sandbox = RecordingSandbox()
    rt = create_runtime("harness", model="x", provider="y")
    await rt.start(sandbox, None)
    assert sandbox.spawn_cmds, "expected spawn"
    cmd = sandbox.spawn_cmds[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "x"
    assert "--provider" in cmd and cmd[cmd.index("--provider") + 1] == "y"
    await rt.stop()


@pytest.mark.asyncio
async def test_u8_n3_harness_start_without_sandbox_fails_loud() -> None:
    rt = create_runtime("harness")
    with pytest.raises(ValueError, match="sandbox"):
        await rt.start(None, None)


@pytest.mark.asyncio
async def test_n4_unsupported_mode_fails_loud() -> None:
    rt = create_runtime("echo")
    await rt.start(None, None)
    with pytest.raises(ValueError, match="unsupported mode"):
        await rt.send("x", command_id="n4", mode="not-prompt")
    await rt.stop()


# --- U10: drift guard ---


@pytest.mark.asyncio
async def test_u10_capabilities_for_agrees_with_runtime_get_capabilities() -> None:
    for name in sorted(known_runtime_names()):
        static = capabilities_for(name)
        rt = create_runtime(name)
        caps = await rt.get_capabilities()
        assert caps.supports_fork == static.supports_fork, name
        assert caps.supports_approvals == static.supports_approvals, name
        assert int(caps.protocol_version) in static.protocol_versions, name
        assert caps.supports_get_state == ("get_state" in static.commands), name
        assert caps.supports_abort == ("abort" in static.commands), name


# --- C4 / A5 / A1: docs + no Cordis ---


def test_c4_a5_agent_core_contract_doc() -> None:
    root = Path(__file__).resolve().parents[4]  # agent-platform/
    doc = root / "contracts" / "agent_core.md"
    assert doc.is_file(), f"missing {doc}"
    text = doc.read_text()
    for needle in (
        "Definition",
        "Provider",
        "Consumer",
        "RuntimeSeams",
        "consumes",
        "not own",
    ):
        assert needle.lower() in text.lower(), f"missing {needle!r} in agent_core.md"


def test_a1_agent_package_has_no_cordis_imports() -> None:
    agent_root = (
        Path(__file__).resolve().parents[2] / "src" / "supervisor" / "agent"
    )
    offenders: list[str] = []
    for path in agent_root.rglob("*.py"):
        body = path.read_text()
        if "cordis" in body.lower() or "from githi" in body or "import githi" in body:
            offenders.append(str(path))
    assert offenders == []


def test_c5_session_journal_api_do_not_import_concrete_runtimes() -> None:
    import ast

    base = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    for pkg in ("session", "journal", "api"):
        for path in (base / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "runtimes" not in node.module, f"{path}: {node.module}"
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "runtimes" not in alias.name, f"{path}: {alias.name}"


def test_a4_only_factory_names_concrete_runtime_classes() -> None:
    """Adding a runtime should not require editing session/journal — only factory."""
    import ast

    factory = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "agent"
        / "factory.py"
    )
    tree = ast.parse(factory.read_text())
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "runtimes" in node.module:
            for alias in node.names or []:
                named.add(alias.name)
    assert "EchoRuntime" in named and "HarnessRuntime" in named
