"""Provider contract: one upstream that serves one or more inference wires.

A wire is a request/response protocol the gateway exposes — OpenAI chat
completions, Anthropic Messages, OpenAI Responses. Each wire is served as a
passthrough: the caller's body goes upstream with only ``model`` rewritten to the
route's upstream id, so provider-native fields (signed thinking blocks, encrypted
reasoning items, ``service_tier``) survive the hop untouched.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Wire(StrEnum):
    CHAT = "chat.completions"
    MESSAGES = "messages"
    RESPONSES = "responses"


type Body = dict[str, Any]
type SSE = Iterator[bytes]
"""A server-sent event stream, relayed byte-for-byte."""


class UpstreamError(Exception):
    """The upstream call failed; ``status`` is the upstream HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProviderUnconfigured(UpstreamError):
    """The provider's vendor credential is not set on the gateway."""


@dataclass(frozen=True, slots=True)
class Call:
    """One upstream request."""

    wire: Wire
    body: Body
    """Caller body with ``model`` already set to the upstream model id."""
    retries: int | None = None
    """Extra attempts on transient failure; ``None`` uses the provider default."""

    @property
    def stream(self) -> bool:
        return self.body.get("stream") is True


@runtime_checkable
class Provider(Protocol):
    name: str
    wires: frozenset[Wire]

    def credential_ok(self) -> bool:
        """Whether the provider can authenticate upstream (readiness)."""
        ...

    def call(self, call: Call) -> Body | SSE:
        """JSON body for a non-streaming call, else the upstream SSE byte stream."""
        ...


class ProviderRegistry(Mapping[str, Provider]):
    """Provider name → implementation; the gateway's only provider lookup."""

    def __init__(self) -> None:
        self._by_name: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if provider.name in self._by_name:
            raise ValueError(f"duplicate provider: {provider.name}")
        self._by_name[provider.name] = provider

    def require(self, name: str) -> Provider:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"provider not registered: {name}") from None

    def __getitem__(self, name: str) -> Provider:
        return self._by_name[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)
