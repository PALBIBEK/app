"""protocols-extract scenarios (PE-U/N/C/I/S)."""

from __future__ import annotations

import ast
import concurrent.futures
import inspect
import threading
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.harness.system_prompt import build_system_prompt
from githi.llm.client import Completion, LLMClient
from githi.llm.streaming import StreamEvent, StreamEventType
from githi.sandbox.policy import SandboxPolicy
from githi.seams import (
    AssembledPrompt,
    ModelGatewayClient,
    PromptAssembler,
    PromptBindings,
    ProviderConflict,
    ProviderMissing,
    SubagentRequest,
    SubagentTransport,
    SubagentTransportImpl,
    ToolDescriptor,
    ToolProvider,
    UnsupportedCapability,
    build_default_seams,
)
from githi.seams.providers.default_prompt import DefaultPromptAssembler
from githi.seams.providers.in_process_subagent import InProcessSubagentProvider
from githi.seams.providers.in_process_tools import InProcessToolProvider
from githi.seams.providers.llm_gateway import LLMGatewayAdapter
from githi.subagent import SubagentError
from githi.tools.base import Tool, ToolContext, ok
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, SystemMessage, Usage

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "githi"
SUPERVISOR = Path(
    "/Users/rahulrawatadmin/Desktop/YAL_AGENT_SERVICE/agent-platform"
    "/services/supervisor/src/supervisor"
)
CONTRACTS = Path(
    "/Users/rahulrawatadmin/Desktop/YAL_AGENT_SERVICE/agent-platform/contracts"
)


class ScriptedLLM:
    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [AssistantMessage(content="done")])
        self.calls: list[Any] = []
        self.model = "test"

    def complete(self, messages, tools, **kw) -> Completion:
        self.calls.append(list(messages))
        msg = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=msg, usage=Usage(input_tokens=1, output_tokens=1))

    def stream(self, messages, tools, interrupt=None, **kw) -> Iterator[StreamEvent]:
        result = self.complete(messages, tools, **kw)
        yield StreamEvent(StreamEventType.TEXT_DELTA, delta=result.message.content or "")
        yield StreamEvent(
            StreamEventType.DONE, message=result.message, usage=result.usage
        )

    def close(self) -> None:
        return None


class EchoProvider:
    def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="echo",
                description="echo",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            )
        ]

    def invoke(self, ctx: ToolContext, name: str, args: dict[str, Any]):
        return ok(str(args.get("text", "")))


class FixedAssembler:
    def __init__(self, text: str) -> None:
        self._text = text

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        return AssembledPrompt(text=self._text)


class FakeGateway:
    def complete(self, messages, tools, **kw) -> Completion:
        return Completion(
            message=AssistantMessage(content="canned"),
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    def stream(self, messages, tools, interrupt=None, **kw) -> Iterator[StreamEvent]:
        yield StreamEvent(StreamEventType.TEXT_DELTA, delta="canned")
        yield StreamEvent(
            StreamEventType.DONE,
            message=AssistantMessage(content="canned"),
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    def close(self) -> None:
        return None


class CapLimitedProvider:
    name = "limited"
    capabilities = frozenset({"spawn"})
    started = False

    def start_one_shot(self, request: SubagentRequest):
        CapLimitedProvider.started = True
        raise AssertionError("must not start")


class OkProvider:
    name = "ok"
    capabilities = frozenset({"spawn"})

    def start_one_shot(self, request: SubagentRequest):
        return "started"


class NoContinuableProvider:
    name = "no_cont"
    capabilities = frozenset({"spawn"})

    def start_one_shot(self, request: SubagentRequest):
        raise AssertionError("unused")

    def start_fork(self, request: SubagentRequest):
        raise AssertionError("unused")


def _echo_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext):
        return ok(str(args.get("text", "")))

    return Tool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        run=run,
    )


def _make_agent(tmp_path: Path, llm=None, **kwargs) -> Agent:
    return Agent(
        llm=llm or ScriptedLLM(),
        tools=ToolRegistry([]),
        policy=SandboxPolicy(root=tmp_path),
        config=AgentConfig(cwd=str(tmp_path)),
        **kwargs,
    )


def test_pe_u01_tool_provider_fake(tmp_path):
    provider: ToolProvider = EchoProvider()
    assert provider.list_tools()[0].name == "echo"
    ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
    assert provider.invoke(ctx, "echo", {"text": "hi"}).text == "hi"
    assert isinstance(provider, ToolProvider)


def test_pe_u02_prompt_assembler_fake(tmp_path):
    fake: PromptAssembler = FixedAssembler("SYS")
    assert fake.assemble(PromptBindings()).text == "SYS"
    agent = _make_agent(tmp_path, prompt_assembler=fake)
    agent.run("hello")
    systems = [m for m in agent.state.messages if isinstance(m, SystemMessage)]
    assert systems and "SYS" in systems[0].content


def test_pe_u03_model_gateway_fake():
    gw: ModelGatewayClient = FakeGateway()
    assert gw.complete([], []).message.content == "canned"
    assert list(gw.stream([], []))
    assert "httpx" not in inspect.getsource(FakeGateway)


def test_pe_u04_named_registry_dispose():
    transport: SubagentTransport = SubagentTransportImpl()
    dispose = transport.register_provider("fake", OkProvider())
    assert transport.start_one_shot("fake", SubagentRequest(prompt="x")) == "started"
    dispose()
    dispose()
    with pytest.raises(ProviderMissing) as ei:
        transport.start_one_shot("fake", SubagentRequest(prompt="x"))
    assert ei.value.code == "PROVIDER_MISSING"
    assert "fake" in str(ei.value)


def test_pe_u05_in_process_tool_provider(tmp_path):
    reg = ToolRegistry([_echo_tool()])
    provider = InProcessToolProvider(reg)
    assert provider.list_tools()[0].name == "echo"
    ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
    assert provider.invoke(ctx, "echo", {"text": "hi"}).text == "hi"
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([_echo_tool(), _echo_tool()])


def test_pe_n01_unsupported_capability():
    CapLimitedProvider.started = False
    transport = SubagentTransportImpl()
    limited = CapLimitedProvider()
    transport.register_provider("in_process", limited)
    with pytest.raises(UnsupportedCapability) as ei:
        transport.start_one_shot(
            "in_process",
            SubagentRequest(prompt="x", tool_filter=["bash"]),
        )
    assert ei.value.code == "UNSUPPORTED_CAPABILITY"
    assert CapLimitedProvider.started is False


def test_pe_n02_missing_provider():
    transport = SubagentTransportImpl()
    transport.register_provider("in_process", OkProvider())
    with pytest.raises(ProviderMissing) as ei:
        transport.start_one_shot("acp", SubagentRequest(prompt="x"))
    assert "acp" in str(ei.value)
    assert ei.value.code == "PROVIDER_MISSING"


def test_pe_n03_continuable_method_presence():
    transport = SubagentTransportImpl()
    transport.register_provider("in_process", NoContinuableProvider())
    with pytest.raises(UnsupportedCapability) as ei:
        transport.start_continuable("in_process", SubagentRequest(prompt="x"))
    assert ei.value.code == "UNSUPPORTED_CAPABILITY"


def test_pe_n04_duplicate_provider_conflict():
    transport = SubagentTransportImpl()
    transport.register_provider("in_process", OkProvider())
    with pytest.raises(ProviderConflict) as ei:
        transport.register_provider("in_process", CapLimitedProvider())
    assert ei.value.code == "PROVIDER_CONFLICT"
    assert transport.start_one_shot("in_process", SubagentRequest(prompt="x")) == "started"


def test_pe_n05_unknown_tool(tmp_path):
    provider = InProcessToolProvider(ToolRegistry([]))
    ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
    result = provider.invoke(ctx, "nope", {})
    assert result.is_error
    assert "nope" in result.text


def test_pe_c01_agent_init_no_registry_construction():
    src = (SRC / "agent" / "agent.py").read_text()
    tree = ast.parse(src)
    init = next(
        item
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Agent"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "__init__"
    )
    text = ast.get_source_segment(src, init) or ""
    for banned in ("SubagentService(", "SubagentJobRegistry(", "ContinuableRegistry("):
        assert banned not in text


def test_pe_c02_agent_avoids_provider_imports():
    src = (SRC / "agent" / "agent.py").read_text()
    assert "in_process_subagent" not in src
    assert "InProcessSubagentProvider" not in src


def test_pe_c03_supervisor_journal_session_api_clean():
    needles = ("githi", "SubagentService", "ToolProvider", "ModelGatewayClient")
    hits: list[str] = []
    for sub in ("journal", "session", "api"):
        folder = SUPERVISOR / sub
        assert folder.is_dir(), folder
        for path in folder.rglob("*.py"):
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if not any(n in line for n in needles):
                    continue
                if "import" in line or line.strip().startswith("from "):
                    hits.append(f"{path}:{i}:{line.strip()}")
    assert hits == []


def test_pe_c04_factory_owns_in_process_construction():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = str(path.relative_to(SRC))
        if rel.startswith("seams/factory.py") or rel.startswith("seams/providers/"):
            continue
        if "InProcessSubagentProvider(" in path.read_text(errors="ignore"):
            offenders.append(rel)
    assert offenders == []


def test_pe_c05_swap_assembler_without_agent_edit(tmp_path):
    a1 = _make_agent(tmp_path / "a", prompt_assembler=FixedAssembler("A"))
    a2 = _make_agent(tmp_path / "b", prompt_assembler=FixedAssembler("B"))
    a1.run("x")
    a2.run("y")
    s1 = next(m for m in a1.state.messages if isinstance(m, SystemMessage))
    s2 = next(m for m in a2.state.messages if isinstance(m, SystemMessage))
    assert "A" in s1.content and "B" in s2.content


def test_pe_c06_mirror_contract_doc():
    path = CONTRACTS / "seams.md"
    assert path.is_file()
    text = path.read_text()
    for name in (
        "ToolProvider",
        "PromptAssembler",
        "SubagentTransport",
        "ModelGatewayClient",
    ):
        assert name in text


def test_pe_i01_cli_build_agent_injects_seams(tmp_path, monkeypatch):
    from githi import cli as cli_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class Patched(ScriptedLLM):
        def __init__(self, *a, **k):
            super().__init__([AssistantMessage(content="child-done")])

    monkeypatch.setattr(cli_mod, "LLMClient", Patched)
    args = cli_mod._parse_args(["--workspace", str(tmp_path), "--no-memory", "hi"])
    agent, _ = cli_mod.build_agent(args)
    assert agent.subagents is not None
    assert agent.subagent_transport is not None
    result = agent.subagents.start_one_shot("say hi", description="t")
    assert result.output


def test_pe_i02_default_assembler_matches_build_system_prompt():
    tools = [_echo_tool()]
    expected = build_system_prompt(
        cwd="/w", tools=tools, skill_listing="SK", extra="EX", custom=""
    )
    got = DefaultPromptAssembler().assemble(
        PromptBindings(
            cwd="/w", tools=tools, skill_listing="SK", extra="EX", custom=""
        )
    ).text
    assert got == expected


def test_pe_i03_llm_adapter_matches_direct_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = LLMClient(
        api_key="k",
        model="m",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    gateway: ModelGatewayClient = LLMGatewayAdapter(client)
    assert (
        gateway.complete([], []).message.content
        == client.complete([], []).message.content
        == "hello"
    )
    client.close()


def test_pe_i04_spawn_and_fork_smoke(tmp_path):
    llm = ScriptedLLM(
        [AssistantMessage(content="child-a"), AssistantMessage(content="child-b")]
    )
    agent = _make_agent(tmp_path, llm=llm)
    r1 = agent.subagent_transport.start_one_shot(
        "in_process", SubagentRequest(prompt="one")
    )
    r2 = agent.subagent_transport.start_fork(
        "in_process", SubagentRequest(prompt="two", mode="fork")
    )
    assert r1.output and r2.output
    with pytest.raises(SubagentError) as ei:
        agent.subagents.start_one_shot("x", parent_depth=99)
    assert ei.value.code == "DEPTH_EXCEEDED"


def test_pe_s01_concurrent_agents_isolated(tmp_path):
    agents = []
    for i in range(8):
        agent = _make_agent(tmp_path / str(i))
        name = f"fake-{i}"

        class Prov:
            capabilities = frozenset({"spawn"})

            def __init__(self, n: str) -> None:
                self._n = n

            def start_one_shot(self, request):
                return self._n

        agent.subagent_transport.register_provider(name, Prov(name))
        agents.append((agent, name))

    def run_one(pair):
        agent, name = pair
        return agent.subagent_transport.start_one_shot(
            name, SubagentRequest(prompt="x")
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_one, agents))
    assert results == [n for _, n in agents]
    with pytest.raises(ProviderMissing):
        agents[0][0].subagent_transport.start_one_shot(
            "fake-1", SubagentRequest(prompt="x")
        )


def test_pe_s02_assembler_isolation(tmp_path):
    a1 = _make_agent(tmp_path / "a", prompt_assembler=FixedAssembler("A"))
    a2 = _make_agent(tmp_path / "b", prompt_assembler=FixedAssembler("B"))
    for _ in range(5):
        assert a1.prompt_assembler.assemble(PromptBindings()).text == "A"
        assert a2.prompt_assembler.assemble(PromptBindings()).text == "B"


def test_pe_s03_background_fan_out(tmp_path):
    llm = ScriptedLLM([AssistantMessage(content=f"c{i}") for i in range(20)])
    agent = _make_agent(tmp_path, llm=llm)
    ids = []
    for i in range(5):
        handle = agent.subagent_transport.start_background(
            "in_process",
            SubagentRequest(prompt=f"job-{i}", description=f"d{i}"),
        )
        ids.append(handle.job_id)
    assert len(ids) == len(set(ids)) == 5
    for jid in ids:
        assert agent.subagent_jobs.get(jid) is not None


def test_pe_s04_gateway_concurrent_complete():
    lock = threading.Lock()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(1)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = LLMClient(
        api_key="k",
        model="m",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    gateway = LLMGatewayAdapter(client)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        out = list(
            pool.map(lambda _: gateway.complete([], []).message.content, range(2))
        )
    assert out == ["ok", "ok"]
    assert len(calls) == 2
    client.close()


def test_build_default_seams_registers_in_process():
    seams = build_default_seams()
    assert isinstance(seams.subagents, SubagentTransportImpl)
    assert InProcessSubagentProvider.name == "in_process"
