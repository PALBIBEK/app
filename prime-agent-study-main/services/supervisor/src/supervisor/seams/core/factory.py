"""Composition root for default RuntimeSeams (sole name→impl map)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Final

from supervisor.agent.seams import RuntimeSeams
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.seams.interfaces.prompt_assembler import PromptAssembler
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient
from supervisor.seams.providers.subagent.memory_subagent import (
    LAB_ECHO_PROVIDER,
    LEGACY_MEMORY_ALIAS,
    LabEchoSubagentProvider,
)
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider
from supervisor.seams.providers.tools.null_tools import NullToolProvider
from supervisor.seams.providers.prompt.static_prompt import StaticPromptAssembler
from supervisor.seams.interfaces.subagent_transport import SubagentTransport, SubagentTransportImpl
from supervisor.seams.interfaces.tool_provider import ToolProvider

if TYPE_CHECKING:
    from supervisor.catalog import MemoryProfile
    from supervisor.seams.interfaces.context_memory import ContextMemoryProvider

# Nested session provider constant
NESTED_SESSION_PROVIDER = "nested_session"

STANDALONE_TOOLS: Final = ("read", "write", "ls", "grep")
"""Offered to a githi runtime created without a tool seam (no catalog pins):
workspace file tools only, never a shell."""


def _env_name() -> str:
    return (os.environ.get("ENV") or os.environ.get("APP_ENV") or "local").strip().lower()


def _staging_or_prod() -> bool:
    return _env_name() in ("staging", "production", "prod")


def lab_echo_allowed(*, explicit: bool | None = None) -> bool:
    """Lab echo is local/tests-only unless ALLOW_LAB_ECHO / explicit override."""
    if explicit is not None:
        return explicit
    flag = (os.environ.get("ALLOW_LAB_ECHO") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    # Staging/prod: never unmarked echo.
    if _staging_or_prod():
        return False
    return True


def build_context_memory_from_profile(
    profile: "MemoryProfile | None",
    gateway: ModelGatewayClient,
) -> "ContextMemoryProvider":
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
    if profile.recall_enabled and profile.recall_backend == "githi_markdown":
        from supervisor.seams.providers.memory.markdown_recall import MarkdownRecallProvider
        recall = MarkdownRecallProvider()
    elif profile.recall_enabled and profile.recall_backend == "mcp":
        url = (profile.recall_mcp_server_url or "").strip()
        if url:
            from supervisor.seams.providers.memory.mcp_recall import McpSemanticRecallProvider
            recall = McpSemanticRecallProvider(server_url=url)
    elif profile.recall_enabled and profile.recall_backend in (
        "durable",
        "memory_service",
    ):
        from supervisor.seams.providers.memory.durable_recall import DurableRecallProvider
        recall = DurableRecallProvider(
            max_tokens=int(getattr(profile, "recall_max_tokens", None) or 512),
        )

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
) -> ModelGatewayClient:
    if model_gateway is not None:
        return model_gateway
    url = gateway_base_url if gateway_base_url is not None else os.environ.get("GATEWAY_BASE_URL")
    if url:
        token = (
            gateway_token
            if gateway_token is not None
            else os.environ.get("GATEWAY_TOKEN") or os.environ.get("MODEL_GATEWAY_TOKEN")
        )
        return HttpModelGatewayClient(base_url=url, token=token or None)
    return FakeModelGatewayClient()


def _build_subagent_transport(
    *,
    gateway: ModelGatewayClient,
    register_lab_echo: bool,
    register_acp: bool,
    acp_base_url: str | None,
) -> SubagentTransport:
    """Wire production-safe defaults: ACP > nested_session > lab_echo (lab only)."""
    acp_url = (acp_base_url or os.environ.get("ACP_BASE_URL") or "").strip()
    want_acp = register_acp or bool(acp_url)

    if want_acp:
        default_name = "acp"
    elif register_lab_echo:
        default_name = LAB_ECHO_PROVIDER
    else:
        default_name = NESTED_SESSION_PROVIDER

    impl = SubagentTransportImpl(default_provider=default_name)

    if register_lab_echo:
        echo = LabEchoSubagentProvider()
        impl.register_provider(LAB_ECHO_PROVIDER, echo)
        # Deprecated alias for older tests — never the staging/prod default.
        impl.register_provider(LEGACY_MEMORY_ALIAS, echo)

    if want_acp:
        from supervisor.seams.providers.subagent.acp_subagent import AcpSubagentProvider

        impl.register_provider(
            "acp",
            AcpSubagentProvider(base_url=acp_url or "http://127.0.0.1:8091"),
        )

    if not want_acp and not register_lab_echo:
        from supervisor.seams.providers.subagent.nested_session import NestedSessionSubagentProvider
        impl.register_provider(
            NESTED_SESSION_PROVIDER,
            NestedSessionSubagentProvider(gateway),
        )
    elif not want_acp and register_lab_echo:
        # Lab path already registered; also offer nested when gateway is real.
        pass
    else:
        # ACP primary — still register nested as optional alternate when not lab.
        if not register_lab_echo:
            try:
                from supervisor.seams.providers.subagent.nested_session import NestedSessionSubagentProvider
                impl.register_provider(
                    NESTED_SESSION_PROVIDER,
                    NestedSessionSubagentProvider(gateway),
                )
            except Exception:
                pass

    return impl


def build_runtime_seams(
    *,
    tool_provider: ToolProvider | None = None,
    prompt_assembler: PromptAssembler | None = None,
    subagent_transport: SubagentTransport | None = None,
    model_gateway: ModelGatewayClient | None = None,
    context_memory: "ContextMemoryProvider | None" = None,
    gateway_base_url: str | None = None,
    gateway_token: str | None = None,
    register_memory: bool | None = None,
    register_lab_echo: bool | None = None,
    register_acp: bool = False,
    mcp_server_url: str | None = None,
    acp_base_url: str | None = None,
) -> RuntimeSeams:
    """Construct default in-process providers for tests / binder hooks.

    **Only** this factory (and tests) may construct Null/Static/Memory/Fake/HTTP
    providers for production-shaped wiring (P-3). Runtimes must not call this.

    When ``model_gateway`` is omitted, prefers ``gateway_base_url`` (or env
    ``GATEWAY_BASE_URL``) → ``HttpModelGatewayClient``; else ``FakeModelGatewayClient``.

    Subagent defaults (D12):
    - Prefer ``acp`` when ``ACP_BASE_URL`` / ``acp_base_url`` / ``register_acp``.
    - Else ``nested_session`` (gateway-backed) in staging/prod.
    - ``lab_echo`` (legacy alias ``memory``) only when lab allowed
      (local/tests, ``ALLOW_LAB_ECHO=1``, or explicit ``register_lab_echo=True``).

    Optional out-of-process plugs (QUEUE ``oop-mcp-acp``):
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
    )

    # register_memory is deprecated alias for register_lab_echo.
    explicit_lab = register_lab_echo if register_lab_echo is not None else register_memory
    allow_lab = lab_echo_allowed(explicit=explicit_lab)

    transport: SubagentTransport
    if subagent_transport is not None:
        transport = subagent_transport
    else:
        transport = _build_subagent_transport(
            gateway=gateway,
            register_lab_echo=allow_lab,
            register_acp=register_acp,
            acp_base_url=acp_base_url,
        )

    memory = context_memory if context_memory is not None else NullContextMemoryProvider()
    return RuntimeSeams(
        tool_provider=tools,
        prompt_assembler=prompts,
        subagent_transport=transport,
        model_gateway=gateway,
        context_memory=memory,
    )


def build_runtime_seams_from_handles(**kwargs: Any) -> RuntimeSeams:
    """Alias kept for binder clarity — same as ``build_runtime_seams``."""
    return build_runtime_seams(**kwargs)
