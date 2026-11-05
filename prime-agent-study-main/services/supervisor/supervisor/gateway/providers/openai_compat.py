"""OpenAI upstream: chat completions and Responses (keys stay in the gateway process)."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from supervisor.gateway.providers.base import SSE, Body, Call, Wire
from supervisor.gateway.providers.http import ApiKey, HttpUpstream

_PATHS: Mapping[Wire, str] = {Wire.CHAT: "/chat/completions", Wire.RESPONSES: "/responses"}


class OpenAICompatProvider:
    """Any OpenAI-compatible upstream; ``base_url`` includes the API's version prefix
    (``/v1`` for OpenAI, ``/api/paas/v4`` for Z.ai). Serves chat completions, and Responses
    when ``responses`` is set (only OpenAI itself serves it)."""

    name = "openai"
    wires: frozenset[Wire] = frozenset(_PATHS)

    def __init__(
        self,
        *,
        api_key: ApiKey,
        base_url: str = "https://api.openai.com/v1",
        transport: httpx.BaseTransport | None = None,
        extra_headers: Mapping[str, str] | None = None,
        retries: int = 1,
        key_env_name: str = "OPENAI_API_KEY",
        name: str | None = None,
        responses: bool = True,
        key_required: bool = True,
        base_url_env_name: str = "OPENAI_BASE_URL",
    ) -> None:
        if name is not None:
            self.name = name
        if not responses:
            self.wires = frozenset({Wire.CHAT})
        self._http = HttpUpstream(
            vendor=self.name,
            api_key=api_key,
            base_url=base_url,
            key_env_name=key_env_name,
            base_url_env_name=base_url_env_name,
            key_required=key_required,
            extra_headers=dict(extra_headers or {}),
            transport=transport,
            retries=retries,
        )

    def credential_ok(self) -> bool:
        return self._http.credential_ok()

    def call(self, call: Call) -> Body | SSE:
        return self._http.post(_PATHS[call.wire], call.body, retries=call.retries)
