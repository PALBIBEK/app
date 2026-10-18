"""SubagentTransport seam + named provider registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from ..subagent import SubagentRunResult
from ..types import Message
from .errors import ProviderConflict, ProviderMissing, UnsupportedCapability

Disposer = Callable[[], None]

# Capability ids requested on SubagentRequest / advertised by providers.
CAP_TOOL_FILTER = "tool_filter"
CAP_PERSONA = "persona"
CAP_DEPTH_LIMIT = "depth_limit"
CAP_OUTPUT_SCHEMA = "output_schema"
CAP_CONTINUABLE = "continuable"
CAP_BACKGROUND = "background"
CAP_FORK = "fork"
CAP_SPAWN = "spawn"


@dataclass
class SubagentRequest:
    """Provider-neutral spawn / fork / background / continuable request."""

    prompt: str
    description: str = ""
    parent_depth: int = 0
    mode: str = "spawn"
    seed: list[Message] | None = None
    interrupt: Any = None
    run_id: str | None = None
    tool_filter: list[str] | None = None
    persona: str | None = None
    depth_limit: int | None = None
    output_schema: dict[str, Any] | None = None


@dataclass
class JobHandle:
    job_id: str


@dataclass
class ChildHandle:
    child_id: str
    message_id: str = ""


@dataclass
class ChildInfo:
    id: str
    status: str
    description: str = ""
    pending: int = 0
    live: bool = False


@runtime_checkable
class SubagentProvider(Protocol):
    """Named implementation behind SubagentTransport."""

    name: str
    capabilities: frozenset[str]

    def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
        ...

    def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
        ...


class NamedProviderRegistry:
    """Multi-provider named registry (DeepSeek registerProvider + disposer)."""

    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}

    def register(self, name: str, provider: Any) -> Disposer:
        if name in self._providers:
            raise ProviderConflict(name)
        self._providers[name] = provider
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            self._providers.pop(name, None)

        return dispose

    def get(self, name: str) -> Any:
        try:
            return self._providers[name]
        except KeyError:
            raise ProviderMissing(name) from None

    def names(self) -> list[str]:
        return list(self._providers)


def _requested_capabilities(request: SubagentRequest) -> list[str]:
    caps: list[str] = []
    if request.tool_filter is not None:
        caps.append(CAP_TOOL_FILTER)
    if request.persona is not None:
        caps.append(CAP_PERSONA)
    if request.depth_limit is not None:
        caps.append(CAP_DEPTH_LIMIT)
    if request.output_schema is not None:
        caps.append(CAP_OUTPUT_SCHEMA)
    return caps


def assert_capabilities(provider: Any, request: SubagentRequest) -> None:
    """Fail loud before child work when request exceeds advertised caps."""
    advertised = frozenset(getattr(provider, "capabilities", frozenset()) or [])
    for cap in _requested_capabilities(request):
        if cap not in advertised:
            raise UnsupportedCapability(
                f"provider does not support capability {cap!r}",
                capability=cap,
            )


@runtime_checkable
class SubagentTransport(Protocol):
    """Definition: named multi-provider subagent transport."""

    def register_provider(self, name: str, provider: Any) -> Disposer:
        ...

    def start_one_shot(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        ...

    def start_fork(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        ...

    def start_background(
        self, provider_name: str, request: SubagentRequest
    ) -> JobHandle:
        ...

    def start_continuable(
        self, provider_name: str, request: SubagentRequest
    ) -> ChildHandle:
        ...

    def send_message(self, child_id: str, text: str) -> None:
        ...

    def interrupt(self, child_id: str) -> None:
        ...

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        ...


class SubagentTransportImpl:
    """In-process transport with a per-Agent named provider registry."""

    def __init__(self) -> None:
        self._registry = NamedProviderRegistry()
        self._parent: Any = None
        self._default_provider = "in_process"

    def bind_parent(self, parent: Any) -> None:
        """Attach parent Agent; bind in-process provider façades if present."""
        self._parent = parent
        try:
            provider = self._registry.get(self._default_provider)
        except ProviderMissing:
            return
        bind = getattr(provider, "bind_parent", None)
        if callable(bind):
            bind(parent)
            # Compatibility façades for existing tools / tests.
            parent.subagents = provider.service
            parent.subagent_jobs = provider.jobs
            parent.subagent_continuable = provider.continuable

    def register_provider(self, name: str, provider: Any) -> Disposer:
        return self._registry.register(name, provider)

    def _provider(self, name: str) -> Any:
        return self._registry.get(name)

    def start_one_shot(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        provider = self._provider(provider_name)
        assert_capabilities(provider, request)
        return provider.start_one_shot(request)

    def start_fork(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        provider = self._provider(provider_name)
        assert_capabilities(provider, request)
        if CAP_FORK not in frozenset(getattr(provider, "capabilities", ())):
            raise UnsupportedCapability(
                "provider does not support capability 'fork'",
                capability=CAP_FORK,
            )
        return provider.start_fork(request)

    def start_background(
        self, provider_name: str, request: SubagentRequest
    ) -> JobHandle:
        provider = self._provider(provider_name)
        assert_capabilities(provider, request)
        start_bg = getattr(provider, "start_background", None)
        if not callable(start_bg):
            raise UnsupportedCapability(
                "provider does not support capability 'background'",
                capability=CAP_BACKGROUND,
            )
        if CAP_BACKGROUND not in frozenset(getattr(provider, "capabilities", ())):
            raise UnsupportedCapability(
                "provider does not support capability 'background'",
                capability=CAP_BACKGROUND,
            )
        return start_bg(request)

    def start_continuable(
        self, provider_name: str, request: SubagentRequest
    ) -> ChildHandle:
        provider = self._provider(provider_name)
        assert_capabilities(provider, request)
        # F-5: method presence IS the capability.
        start_c = getattr(provider, "start_continuable", None)
        if not callable(start_c):
            raise UnsupportedCapability(
                "provider does not support continuable "
                "(missing start_continuable method)",
                capability=CAP_CONTINUABLE,
            )
        return start_c(request)

    def send_message(self, child_id: str, text: str) -> None:
        provider = self._provider(self._default_provider)
        provider.send_message(child_id, text)

    def interrupt(self, child_id: str) -> None:
        provider = self._provider(self._default_provider)
        provider.interrupt(child_id)

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        provider = self._provider(self._default_provider)
        return provider.list_children(parent_id)
