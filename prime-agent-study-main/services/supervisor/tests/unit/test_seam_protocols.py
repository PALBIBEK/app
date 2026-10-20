"""Seam-protocols — Definitions, fakes, fail-loud, contract gates (QUEUE step 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _supervisor_src() -> Path:
    return Path(__file__).resolve().parents[2] / "supervisor"


def _agent_platform_root() -> Path:
    return Path(__file__).resolve().parents[4]


# --- SP-U01 ---


def test_sp_u01_tool_provider_fake_lists_and_invokes() -> None:
    from supervisor.seams import ToolDescriptor, ToolInvokeContext, ToolProvider, ToolResult

    class EchoToolProvider:
        def list_tools(self) -> list[ToolDescriptor]:
            return [
                ToolDescriptor(
                    name="echo",
                    description="echo text",
                    parameters={"type": "object", "properties": {"text": {"type": "string"}}},
                )
            ]

        def invoke(
            self, ctx: ToolInvokeContext, name: str, args: dict[str, Any]
        ) -> ToolResult:
            assert name == "echo"
            return ToolResult(ok=True, payload=args.get("text", ""))

    provider: ToolProvider = EchoToolProvider()
    tools = provider.list_tools()
    assert len(tools) == 1 and tools[0].name == "echo"
    result = provider.invoke(ToolInvokeContext(trace_id="t1"), "echo", {"text": "hi"})
    assert result.ok is True
    assert result.payload == "hi"


# --- Fail loud ---


# --- Contract / architecture ---


def test_sp_c01_runtimes_do_not_construct_providers() -> None:
    root = _supervisor_src() / "agent" / "runtimes"
    banned = (
        "NullToolProvider(",
        "FakeModelGatewayClient(",
        "SubagentTransportImpl(",
        "build_runtime_seams(",
        "StaticPromptAssembler(",
        "MemorySubagentProvider(",
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        body = path.read_text()
        for needle in banned:
            if needle in body:
                offenders.append(f"{path}:{needle}")
    assert offenders == []


def test_sp_c02_consumers_import_protocols_not_providers() -> None:
    import ast

    base = _supervisor_src() / "agent"
    paths = [base / "seams.py", base / "factory.py", *(base / "runtimes").rglob("*.py")]
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "seams.providers" not in node.module, f"{path}: {node.module}"


def test_sp_c03_journal_session_api_free_of_provider_concretes() -> None:
    base = _supervisor_src()
    banned = (
        "NullToolProvider",
        "FakeModelGatewayClient",
        "StaticPromptAssembler",
        "MemorySubagentProvider",
        "NullContextMemoryProvider",
        "BasicCompactionProvider",
        "githi.seams",
        "seams.providers",
    )
    for pkg in ("journal", "session", "api"):
        for path in (base / pkg).rglob("*.py"):
            body = path.read_text()
            for needle in banned:
                assert needle not in body, f"{path}: found {needle}"


def test_sp_a03_journal_schemas_remain_provider_type_free() -> None:
    journal = _supervisor_src() / "journal"
    banned = (
        "ToolProvider",
        "PromptAssembler",
        "SubagentTransport",
        "ModelGatewayClient",
        "NullToolProvider",
        "FakeModelGatewayClient",
    )
    for path in journal.rglob("*.py"):
        body = path.read_text()
        for needle in banned:
            assert needle not in body, f"{path}: {needle}"


def test_sp_a04_model_gateway_tree_independent() -> None:
    """QUEUE model-gateway: sibling exists and must not import supervisor."""
    root = _agent_platform_root() / "services" / "model-gateway"
    assert root.is_dir(), "model-gateway sibling service required"
    src = root / "model_gateway"
    assert src.is_dir()
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        body = path.read_text()
        if "import supervisor" in body or "from supervisor" in body:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"gateway must not import supervisor: {offenders}"


# --- Integration ---


# --- Scale ---


