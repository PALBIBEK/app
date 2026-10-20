"""OpenAI upstream: chat completions and Responses (keys stay in the gateway process)."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from supervisor.gateway.providers.base import SSE, Body, Call, Wire
from supervisor.gateway.providers.http import HttpUpstream

_PATHS: Mapping[Wire, str] = {Wire.CHAT: "/chat/completions", Wire.RESPONSES: "/responses"}


class OpenAICompatProvider:
    """Any OpenAI-compatible upstream; ``base_url`` includes the ``/v1`` prefix."""

    name = "openai"
    wires: frozenset[Wire] = frozenset(_PATHS)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        transport: httpx.BaseTransport | None = None,
        extra_headers: Mapping[str, str] | None = None,
        retries: int = 1,
        key_env_name: str = "OPENAI_API_KEY",
    ) -> None:
        self._http = HttpUpstream(
            vendor=self.name,
            api_key=api_key,
            base_url=base_url,
            key_env_name=key_env_name,
            extra_headers=dict(extra_headers or {}),
            transport=transport,
            retries=retries,
        )

    def credential_ok(self) -> bool:
        return self._http.credential_ok()

    def call(self, call: Call) -> Body | SSE:
        return self._http.post(_PATHS[call.wire], call.body, retries=call.retries)
