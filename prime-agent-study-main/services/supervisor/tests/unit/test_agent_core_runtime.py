"""Agent-core-runtime seam — factory, capabilities, RuntimeSeams (QUEUE step 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.factory import create_runtime, known_runtime_names
from supervisor.agent.seams import RuntimeSeams
from supervisor.harness import load_supported_protocol_versions
from supervisor.harness.services.compat_checker import capabilities_for

# --- U3 / U9 / N2: factory loud fail + known names ---


# --- U1 / U4: echo get_capabilities on Protocol surface ---


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


def test_a3_seams_are_per_runtime_not_global() -> None:
    a = RuntimeSeams(tool_provider=object())
    b = RuntimeSeams(tool_provider=object())
    rt_a = create_runtime("echo", seams=a)
    rt_b = create_runtime("echo", seams=b)
    assert rt_a.seams is a
    assert rt_b.seams is b
    assert rt_a.seams is not rt_b.seams
    assert rt_a.seams.tool_provider is not rt_b.seams.tool_provider


# --- N4: mode ---


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
    assert "EchoRuntime" in named and "GithiRuntime" in named
