"""Composition root for default RuntimeSeams (sole name→impl map)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final

from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog.core.types import RecallBackend
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.seams.interfaces.prompt_assembler import PromptAssembler
from supervisor.seams.interfaces.tool_provider import ToolProvider
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider
from supervisor.seams.providers.prompt.static_prompt import StaticPromptAssembler
from supervisor.seams.providers.tools.null_tools import NullToolProvider

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from supervisor.catalog import MemoryProfile
    from supervisor.seams.interfaces.context_memory import ContextMemoryProvider

DEFAULT_RECALL_MAX_TOKENS: Final = 512
"""Recall's share of the context when a memory profile does not set ``recall.max_tokens``."""

STANDALONE_TOOLS: Final = ("read", "write", "ls", "grep")
"""Offered to a githi runtime created without a tool seam (no catalog pins):
workspace file tools only, never a shell."""


def build_context_memory_from_profile(
    profile: MemoryProfile | None,
    gateway: ModelGatewayClient,
) -> ContextMemoryProvider:
    """Map pinned ``MemoryProfile`` → ContextMemoryProvider (binder / factory only).

    No profile → ``NullContextMemoryProvider``. Compaction and/or recall overlays
    compose via ``CompositeContextMemoryProvider`` when both are active.
    """
    # Lazy imports to avoid circular dependencies
    from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider

    if profile is None:
        return NullContextMemoryProvider()

    compaction: ContextMemoryProvider
    if profile.compaction_enabled:
        from supervisor.seams.providers.memory.basic_compaction import BasicCompactionProvider
        compaction = BasicCompactionProvider(
            gateway,
            summarizer_model=profile.summarization_model,
        )
    else:
        compaction = NullContextMemoryProvider(rpc_configured=True)

    recall: ContextMemoryProvider | None = None
    if profile.recall_enabled and profile.recall_backend == RecallBackend.GITHI_MARKDOWN:
        from supervisor.seams.providers.memory.markdown_recall import MarkdownRecallProvider
        recall = MarkdownRecallProvider()
    elif profile.recall_enabled and profile.recall_backend == RecallBackend.MCP:
        url = (profile.recall_mcp_server_url or "").strip()
        if url:
            from supervisor.seams.providers.memory.mcp_recall import McpSemanticRecallProvider
            recall = McpSemanticRecallProvider(server_url=url)
    elif profile.recall_enabled and profile.recall_backend in (RecallBackend.DURABLE, RecallBackend.MEMORY_SERVICE):
        from supervisor.seams.providers.memory.durable_recall import DurableRecallProvider
        recall = DurableRecallProvider(max_tokens=profile.recall_max_tokens or DEFAULT_RECALL_MAX_TOKENS)

    if recall is not None:
        from supervisor.seams.providers.memory.composite_memory import (
            CompositeContextMemoryProvider,
        )
        return CompositeContextMemoryProvider(compaction=compaction, recall=recall)
    return compaction


def build_standalone_tool_provider() -> ToolProvider:
    """Tools for a runtime created without catalog pins (local dev, tests)."""
    from supervisor.seams.providers.tools.registry_tools import RegistryToolProvider

    return RegistryToolProvider.of(*STANDALONE_TOOLS)


def _maybe_mcp_tools(
    tool_provider: ToolProvider | None,
    mcp_server_url: str | None,
) -> ToolProvider:
    url = (mcp_server_url or os.environ.get("MCP_SERVER_URL") or "").strip()
    if not url:
        return tool_provider if tool_provider is not None else NullToolProvider()
    from supervisor.seams.providers.tools.composite_tools import CompositeToolProvider
    from supervisor.seams.providers.tools.mcp_tools import McpToolProvider

    mcp = McpToolProvider(server_url=url)
    if tool_provider is None:
        return mcp
    return CompositeToolProvider(tool_provider, mcp)


def _resolve_gateway(
    *,
    model_gateway: ModelGatewayClient | None,
    gateway_base_url: str | None,
    gateway_token: str | None,
    session_id: str | None = None,
    default_model: str | None = None,
) -> ModelGatewayClient:
    """The session's model access, most specific first.

    An explicit client wins. Then the process's routed model gateway
    (``MODEL_GATEWAY_URL`` — discovery, the native registry, three wire protocols),
    unless the composition pins a *different* gateway; then the pinned gateway's
    (or ``GATEWAY_BASE_URL``'s) chat-completions client; else the offline fake.
    ``gateway_token`` is the session's key and ``default_model`` its pinned model
    either way, so a seam that calls the model without naming one (the compaction
    summarizer) uses the session's model.
    """
    if model_gateway is not None:
        return model_gateway
    token = (
        gateway_token
        if gateway_token is not None
        else os.environ.get("GATEWAY_TOKEN") or os.environ.get("MODEL_GATEWAY_TOKEN")
    )
    from supervisor.models import RoutedModelClient, model_router

    router = model_router()
    if router is not None and (gateway_base_url is None or router.serves(gateway_base_url)):
        return RoutedModelClient(
            router, api_key=gateway_token, session_id=session_id, default_model=default_model
        )
    url = gateway_base_url if gateway_base_url is not None else os.environ.get("GATEWAY_BASE_URL")
    if url:
        return HttpModelGatewayClient(base_url=url, token=token or None, default_model=default_model)
    return FakeModelGatewayClient()


def build_runtime_seams(
    *,
    tool_provider: ToolProvider | None = None,
    prompt_assembler: PromptAssembler | None = None,
    model_gateway: ModelGatewayClient | None = None,
    context_memory: ContextMemoryProvider | None = None,
    gateway_base_url: str | None = None,
    gateway_token: str | None = None,
    session_id: str | None = None,
    default_model: str | None = None,
    mcp_server_url: str | None = None,
) -> RuntimeSeams:
    """Construct default in-process providers for tests / binder hooks.

    **Only** this factory (and tests) may construct Null/Static/Memory/Fake/HTTP
    providers for production-shaped wiring (P-3). Runtimes must not call this.

    When ``model_gateway`` is omitted, see ``_resolve_gateway``: a pinned
    ``gateway_base_url``, else the routed model gateway (``MODEL_GATEWAY_URL``),
    else env ``GATEWAY_BASE_URL`` → ``HttpModelGatewayClient``, else the fake.

    Subagents are not a seam: they are sessions (``supervisor.session.children``).

    Optional out-of-process plug:
    ``MCP_SERVER_URL`` / ``mcp_server_url`` → ``McpToolProvider`` (composed with
    ``tool_provider`` when both set).
    """
    tools: ToolProvider = _maybe_mcp_tools(tool_provider, mcp_server_url)
    prompts: PromptAssembler = (
        prompt_assembler
        if prompt_assembler is not None
        else StaticPromptAssembler("YAL default system prompt")
    )
    gateway = _resolve_gateway(
        model_gateway=model_gateway,
        gateway_base_url=gateway_base_url,
        gateway_token=gateway_token,
        session_id=session_id,
        default_model=default_model,
    )

    memory = context_memory if context_memory is not None else NullContextMemoryProvider()
    return RuntimeSeams(
        tool_provider=tools,
        prompt_assembler=prompts,
        model_gateway=gateway,
        context_memory=memory,
    )


