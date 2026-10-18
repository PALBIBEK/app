"""Composition root for default in-process seams (sole name→impl map)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .model_gateway import ModelGatewayClient
from .prompt_assembler import PromptAssembler
from .providers.default_prompt import DefaultPromptAssembler
from .providers.in_process_subagent import InProcessSubagentProvider
from .providers.in_process_tools import InProcessToolProvider
from .providers.llm_gateway import LLMGatewayAdapter
from .subagent_transport import SubagentTransport, SubagentTransportImpl
from .tool_provider import ToolProvider

if TYPE_CHECKING:
    from ..agent.agent import Agent
    from ..llm.client import LLMClient
    from ..tools.registry import ToolRegistry


@dataclass
class Seams:
    """Bundled default seams for one Agent instance."""

    subagents: SubagentTransport
    prompt_assembler: PromptAssembler
    gateway: ModelGatewayClient | Any | None = None
    tools: ToolProvider | None = None

    def bind_parent(self, parent: Agent) -> None:
        """Bind in-process provider registries to `parent` and set façades."""
        bind = getattr(self.subagents, "bind_parent", None)
        if callable(bind):
            bind(parent)


def build_default_seams(
    *,
    llm: Any | None = None,
    tools_registry: ToolRegistry | None = None,
) -> Seams:
    """Construct default in-process providers.

    **Only** this factory (and tests) may construct `InProcessSubagentProvider`.
    Call `seams.bind_parent(agent)` after constructing Agent so registries are
    not built inside `Agent.__init__` source (P-3 / A-4).
    """
    transport = SubagentTransportImpl()
    in_process = InProcessSubagentProvider()
    transport.register_provider("in_process", in_process)

    gateway: ModelGatewayClient | Any | None = None
    if llm is not None:
        from ..llm.client import LLMClient

        if isinstance(llm, LLMClient):
            gateway = LLMGatewayAdapter(llm)
        else:
            gateway = llm  # duck-typed scripted fakes

    tool_provider = (
        InProcessToolProvider(tools_registry) if tools_registry is not None else None
    )

    return Seams(
        subagents=transport,
        prompt_assembler=DefaultPromptAssembler(),
        gateway=gateway,
        tools=tool_provider,
    )


def wire_agent(
    agent: Agent,
    seams: Seams | None = None,
    *,
    llm: Any | None = None,
) -> Agent:
    """Bind default (or provided) seams onto an Agent."""
    if seams is None:
        seams = build_default_seams(llm=llm if llm is not None else agent.llm)
    seams.bind_parent(agent)
    agent.prompt_assembler = seams.prompt_assembler
    if seams.gateway is not None:
        agent.gateway = seams.gateway
    agent.subagent_transport = seams.subagents
    return agent
