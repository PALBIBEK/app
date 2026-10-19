"""SubagentTransport seam + named provider registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from supervisor.seams.core.errors import (
    BadArgs,
    ProviderConflict,
    ProviderMissing,
    UnsupportedCapability,
)
from supervisor.seams.core.types import (
    ChildHandle,
    ChildInfo,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
)

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


def validate_request(request: SubagentRequest) -> None:
    """Reject empty/invalid requests before provider start (F-3, C-6)."""
    if not (request.prompt or "").strip():
        raise BadArgs("subagent request prompt must be non-empty")
    if request.parent_depth < 0:
        raise BadArgs(f"invalid parent_depth={request.parent_depth}")
    if request.depth_limit is not None and request.depth_limit < 0:
        raise BadArgs(f"invalid depth_limit={request.depth_limit}")


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

    def check_background(self, provider_name: str, request: SubagentRequest) -> None:
        """Raise what start_background would, without starting anything."""
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
    """In-process transport with a per-instance named provider registry (H-3)."""

    def __init__(self, *, default_provider: str | None = None) -> None:
        self._registry = NamedProviderRegistry()
        self._default_provider = default_provider
        self._children: dict[str, ChildInfo] = {}

    def register_provider(self, name: str, provider: Any) -> Disposer:
        return self._registry.register(name, provider)

    @property
    def default_provider(self) -> str | None:
        """The provider a request that names none goes to: as wired, else the first registered."""
        names = self._registry.names()
        return self._default_provider or (names[0] if names else None)

    def _provider(self, name: str) -> Any:
        return self._registry.get(name)

    def _prepare(self, provider_name: str, request: SubagentRequest) -> Any:
        validate_request(request)
        provider = self._provider(provider_name)
        assert_capabilities(provider, request)
        return provider

    def start_one_shot(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        provider = self._prepare(provider_name, request)
        return provider.start_one_shot(request)

    def start_fork(
        self, provider_name: str, request: SubagentRequest
    ) -> SubagentRunResult:
        provider = self._prepare(provider_name, request)
        if CAP_FORK not in frozenset(getattr(provider, "capabilities", ())):
            raise UnsupportedCapability(
                "provider does not support capability 'fork'",
                capability=CAP_FORK,
            )
        return provider.start_fork(request)

    def start_background(
        self, provider_name: str, request: SubagentRequest
    ) -> JobHandle:
        return self._background_provider(provider_name, request).start_background(request)

    def check_background(self, provider_name: str, request: SubagentRequest) -> None:
        self._background_provider(provider_name, request)

    def _background_provider(self, provider_name: str, request: SubagentRequest) -> Any:
        provider = self._prepare(provider_name, request)
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
        return provider

    def start_continuable(
        self, provider_name: str, request: SubagentRequest
    ) -> ChildHandle:
        provider = self._prepare(provider_name, request)
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
        name = self._default_provider or (self._registry.names()[0] if self._registry.names() else None)
        if name is None:
            raise ProviderMissing("<default>")
        provider = self._provider(name)
        send = getattr(provider, "send_message", None)
        if not callable(send):
            raise UnsupportedCapability(
                "provider does not support send_message",
                capability="send_message",
            )
        send(child_id, text)

    def interrupt(self, child_id: str) -> None:
        name = self._default_provider or (self._registry.names()[0] if self._registry.names() else None)
        if name is None:
            raise ProviderMissing("<default>")
        provider = self._provider(name)
        interrupt = getattr(provider, "interrupt", None)
        if not callable(interrupt):
            raise UnsupportedCapability(
                "provider does not support interrupt",
                capability="interrupt",
            )
        interrupt(child_id)

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        name = self._default_provider or (self._registry.names()[0] if self._registry.names() else None)
        if name is None:
            return []
        provider = self._provider(name)
        list_fn = getattr(provider, "list_children", None)
        if not callable(list_fn):
            return list(self._children.values())
        return list_fn(parent_id)
