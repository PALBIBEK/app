"""Seam-protocols — Definitions, fakes, fail-loud, contract gates (QUEUE step 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _supervisor_src() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "supervisor"


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


def test_sp_u02_prompt_assembler_static_text() -> None:
    from supervisor.seams import AssembledPrompt, PromptAssembler, PromptBindings
    from supervisor.seams.providers.static_prompt import StaticPromptAssembler

    assembler: PromptAssembler = StaticPromptAssembler("SYS")
    result = assembler.assemble(PromptBindings(cwd="/tmp"))
    assert isinstance(result, AssembledPrompt)
    assert result.text == "SYS"
    assert result.content_digest is not None
    again = assembler.assemble(PromptBindings(cwd="/other"))
    assert again.content_digest == result.content_digest


def test_sp_u05_null_tool_provider_unknown_tool_fails_loud() -> None:
    from supervisor.seams import ToolInvokeContext
    from supervisor.seams.providers.null_tools import NullToolProvider

    provider = NullToolProvider()
    assert provider.list_tools() == []
    result = provider.invoke(ToolInvokeContext(), "nope", {})
    assert result.ok is False
    assert result.code == "TOOL_NOT_FOUND"
    assert "nope" in (result.error or "")


def test_sp_u03_model_gateway_fake_complete_stream() -> None:
    from supervisor.seams import ChatMessage, Completion, ModelGatewayClient, StreamEvent
    from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient

    gw: ModelGatewayClient = FakeModelGatewayClient(assistant_text="hello", model="fake-model")
    completion = gw.complete([ChatMessage(role="user", content="hi")], tools=[])
    assert isinstance(completion, Completion)
    assert completion.message.role == "assistant"
    assert completion.message.content == "hello"
    assert completion.model == "fake-model"

    events = list(gw.stream([ChatMessage(role="user", content="hi")], tools=[]))
    assert events
    assert all(isinstance(e, StreamEvent) for e in events)
    assert "".join(e.delta for e in events if e.type == "delta") == "hello"
    gw.close()

    import ast

    def_mod = _supervisor_src() / "seams" / "model_gateway.py"
    tree = ast.parse(def_mod.read_text())
    banned = ("openai", "anthropic", "httpx", "litellm")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(b in alias.name for b in banned), alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(b in node.module for b in banned), node.module


def test_sp_u04_subagent_transport_register_and_disposer() -> None:
    from supervisor.seams import (
        ProviderMissing,
        SubagentRequest,
        SubagentRunResult,
        SubagentTransport,
    )
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    class FakeProvider:
        name = "fake"
        capabilities: frozenset[str] = frozenset({"spawn"})

        def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
            return SubagentRunResult(text=request.prompt, status="ok")

        def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
            return SubagentRunResult(text=request.prompt, status="ok")

    transport: SubagentTransport = SubagentTransportImpl()
    dispose = transport.register_provider("fake", FakeProvider())
    result = transport.start_one_shot("fake", SubagentRequest(prompt="go"))
    assert result.text == "go"
    dispose()
    dispose()  # idempotent
    with pytest.raises(ProviderMissing) as ei:
        transport.start_one_shot("fake", SubagentRequest(prompt="x"))
    assert ei.value.code == "PROVIDER_MISSING"
    assert ei.value.provider_name == "fake"


def test_sp_u06_build_runtime_seams_returns_typed_bag() -> None:
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams import (
        ContextMemoryProvider,
        ModelGatewayClient,
        PromptAssembler,
        SubagentTransport,
        ToolProvider,
    )
    from supervisor.seams.factory import build_runtime_seams

    seams = build_runtime_seams()
    assert isinstance(seams, RuntimeSeams)
    assert isinstance(seams.tool_provider, ToolProvider)
    assert isinstance(seams.prompt_assembler, PromptAssembler)
    assert isinstance(seams.subagent_transport, SubagentTransport)
    assert isinstance(seams.model_gateway, ModelGatewayClient)
    assert isinstance(seams.context_memory, ContextMemoryProvider)


@pytest.mark.asyncio
async def test_sp_u07_runtime_seams_accepts_typed_protocols() -> None:
    from supervisor.agent.factory import create_runtime
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.factory import build_runtime_seams

    seams = build_runtime_seams()
    assert isinstance(seams, RuntimeSeams)
    rt = create_runtime("echo", seams=seams)
    assert rt.seams is seams
    assert rt.seams.tool_provider is seams.tool_provider
    await rt.start(None, None)
    await rt.send("hi", command_id="u07")
    await rt.stop()


# --- Fail loud ---


def test_sp_n01_unsupported_capability_before_start() -> None:
    from supervisor.seams import SubagentRequest, UnsupportedCapability
    from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    provider = MemorySubagentProvider()
    transport.register_provider("memory", provider)
    with pytest.raises(UnsupportedCapability) as ei:
        transport.start_one_shot(
            "memory",
            SubagentRequest(prompt="go", tool_filter=["bash"]),
        )
    assert ei.value.code == "UNSUPPORTED_CAPABILITY"
    assert ei.value.capability == "tool_filter"
    assert provider.started is False


def test_sp_n02_missing_provider_name() -> None:
    from supervisor.seams import ProviderMissing, SubagentRequest
    from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    transport.register_provider("memory", MemorySubagentProvider())
    with pytest.raises(ProviderMissing) as ei:
        transport.start_one_shot("acp", SubagentRequest(prompt="go"))
    assert ei.value.code == "PROVIDER_MISSING"
    assert "acp" in str(ei.value)


def test_sp_n03_continuable_without_method_presence() -> None:
    from supervisor.seams import SubagentRequest, UnsupportedCapability
    from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    provider = MemorySubagentProvider()
    assert not callable(getattr(provider, "start_continuable", None))
    transport.register_provider("memory", provider)
    with pytest.raises(UnsupportedCapability) as ei:
        transport.start_continuable("memory", SubagentRequest(prompt="go"))
    assert ei.value.code == "UNSUPPORTED_CAPABILITY"
    assert ei.value.capability == "continuable"
    assert provider.started is False


def test_sp_n04_duplicate_provider_registration() -> None:
    from supervisor.seams import ProviderConflict, SubagentRequest
    from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    first = MemorySubagentProvider()
    transport.register_provider("memory", first)
    with pytest.raises(ProviderConflict) as ei:
        transport.register_provider("memory", MemorySubagentProvider())
    assert ei.value.code == "PROVIDER_CONFLICT"
    result = transport.start_one_shot("memory", SubagentRequest(prompt="keep"))
    assert result.text == "keep"
    assert first.started is True


def test_sp_n05_bad_args_on_empty_request() -> None:
    from supervisor.seams import BadArgs, SubagentRequest
    from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    transport = SubagentTransportImpl()
    provider = MemorySubagentProvider()
    transport.register_provider("memory", provider)
    with pytest.raises(BadArgs) as ei:
        transport.start_one_shot("memory", SubagentRequest(prompt=""))
    assert ei.value.code == "BAD_ARGS"
    assert provider.started is False
    with pytest.raises(BadArgs):
        transport.start_one_shot(
            "memory", SubagentRequest(prompt="x", parent_depth=-1)
        )


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


def test_sp_c04_no_githi_cordis_in_seams_package() -> None:
    root = _supervisor_src() / "seams"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        body = path.read_text().lower()
        if "githi" in body or "cordis" in body or "yal.githi" in body:
            offenders.append(str(path))
    assert offenders == []


@pytest.mark.asyncio
async def test_sp_c05_swap_prompt_assembler_without_editing_runtime_ctor() -> None:
    from supervisor.agent.factory import create_runtime
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.providers.static_prompt import StaticPromptAssembler

    a = StaticPromptAssembler("A")
    b = StaticPromptAssembler("B")
    rt_a = create_runtime("echo", seams=RuntimeSeams(prompt_assembler=a))
    rt_b = create_runtime("echo", seams=RuntimeSeams(prompt_assembler=b))
    assert rt_a.seams is not None and rt_a.seams.prompt_assembler is a
    assert rt_b.seams is not None and rt_b.seams.prompt_assembler is b
    await rt_a.start(None, None)
    await rt_b.start(None, None)
    await rt_a.stop()
    await rt_b.stop()


def test_sp_c06_contracts_seams_md_lists_protocols_and_codes() -> None:
    doc = _agent_platform_root() / "contracts" / "seams.md"
    text = doc.read_text()
    for needle in (
        "ToolProvider",
        "PromptAssembler",
        "SubagentTransport",
        "ModelGatewayClient",
        "UNSUPPORTED_CAPABILITY",
        "PROVIDER_MISSING",
        "PROVIDER_CONFLICT",
        "BAD_ARGS",
        "DEPTH_EXCEEDED",
    ):
        assert needle in text, f"missing {needle} in contracts/seams.md"


def test_sp_a01_definition_modules_have_no_provider_imports() -> None:
    import ast

    seams = _supervisor_src() / "seams"
    for name in (
        "tool_provider.py",
        "prompt_assembler.py",
        "subagent_transport.py",
        "model_gateway.py",
    ):
        path = seams / name
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "providers" not in node.module, f"{path}: {node.module}"


def test_sp_a02_factory_is_sole_default_concrete_map() -> None:
    base = _supervisor_src()
    constructors = (
        "MemorySubagentProvider(",
        "FakeModelGatewayClient(",
        "StaticPromptAssembler(",
        "NullToolProvider(",
    )
    allowed_prefixes = (
        str(base / "seams" / "factory.py"),
        str(base / "seams" / "providers"),
    )
    offenders: list[str] = []
    for path in base.rglob("*.py"):
        path_s = str(path)
        if any(path_s.startswith(p) for p in allowed_prefixes):
            continue
        body = path.read_text()
        for needle in constructors:
            if needle in body:
                offenders.append(f"{path}:{needle}")
    assert offenders == []


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
    src = root / "src"
    assert src.is_dir()
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        body = path.read_text()
        if "import supervisor" in body or "from supervisor" in body:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"gateway must not import supervisor: {offenders}"


# --- Integration ---


@pytest.mark.asyncio
async def test_sp_i01_echo_still_works_with_seams_none() -> None:
    from supervisor.agent.factory import create_runtime

    rt = create_runtime("echo", seams=None)
    await rt.start(None, None)
    await rt.send("parity", command_id="i01")
    state = await rt.get_state()
    assert state.session_id
    await rt.stop()


@pytest.mark.asyncio
async def test_sp_i02_echo_stores_fake_gateway_without_calling() -> None:
    from supervisor.agent.factory import create_runtime
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient

    gw = FakeModelGatewayClient(assistant_text="unused")
    seams = RuntimeSeams(model_gateway=gw)
    rt = create_runtime("echo", seams=seams)
    assert rt.seams is not None
    assert rt.seams.model_gateway is gw
    assert gw._closed is False
    await rt.start(None, None)
    await rt.send("hi", command_id="i02")
    await rt.stop()
    assert gw._closed is False


@pytest.mark.asyncio
async def test_sp_i03_pi_starts_with_sandbox_seams_optional() -> None:
    from supervisor.agent.factory import create_runtime
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.providers.static_prompt import StaticPromptAssembler
    from tests.support.replay import ReplaySandbox

    seams = RuntimeSeams(prompt_assembler=StaticPromptAssembler("PI"))
    rt = create_runtime("harness", seams=seams)
    sandbox = ReplaySandbox()
    await rt.start(sandbox, None)
    assert rt.seams is seams
    await rt.stop()


# --- Scale ---


def test_sp_s01_n_transports_do_not_share_registry() -> None:
    from supervisor.seams import ProviderMissing, SubagentRequest, SubagentRunResult
    from supervisor.seams.subagent_transport import SubagentTransportImpl

    class Named:
        def __init__(self, name: str) -> None:
            self.name = name
            self.capabilities = frozenset({"spawn"})

        def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
            return SubagentRunResult(text=self.name)

        def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
            return SubagentRunResult(text=self.name)

    n = 16
    transports = [SubagentTransportImpl() for _ in range(n)]
    disposers = [
        t.register_provider(f"p{i}", Named(f"p{i}")) for i, t in enumerate(transports)
    ]
    disposers[3]()
    with pytest.raises(ProviderMissing):
        transports[3].start_one_shot("p3", SubagentRequest(prompt="x"))
    result = transports[7].start_one_shot("p7", SubagentRequest(prompt="x"))
    assert result.text == "p7"


def test_sp_s02_n_runtime_seams_bags_independent() -> None:
    from supervisor.agent.factory import create_runtime
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.seams.providers.null_tools import NullToolProvider

    n = 8
    providers = [NullToolProvider() for _ in range(n)]
    runtimes = [
        create_runtime("echo", seams=RuntimeSeams(tool_provider=p)) for p in providers
    ]
    for i, rt in enumerate(runtimes):
        assert rt.seams is not None
        assert rt.seams.tool_provider is providers[i]
    assert len({id(rt.seams.tool_provider) for rt in runtimes}) == n
