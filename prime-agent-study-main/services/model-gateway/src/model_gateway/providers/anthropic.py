"""Anthropic upstream: native Messages, plus its OpenAI-compatible chat completions."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from model_gateway.providers.base import SSE, Body, Call, Wire
from model_gateway.providers.http import AuthHeaders, HttpUpstream, bearer

ANTHROPIC_VERSION = "2023-06-01"
_PATHS: Mapping[Wire, str] = {Wire.MESSAGES: "/v1/messages", Wire.CHAT: "/v1/chat/completions"}


def _x_api_key(key: str) -> Mapping[str, str]:
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}


class AnthropicProvider:
    """``base_url`` is the API origin without ``/v1`` (as ``ANTHROPIC_BASE_URL`` is everywhere)."""

    name = "anthropic"
    wires: frozenset[Wire] = frozenset(_PATHS)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        transport: httpx.BaseTransport | None = None,
        retries: int = 1,
    ) -> None:
        def leg(auth: AuthHeaders) -> HttpUpstream:
            return HttpUpstream(
                vendor=self.name,
                api_key=api_key,
                base_url=base_url,
                key_env_name="ANTHROPIC_API_KEY",
                auth=auth,
                transport=transport,
                retries=retries,
            )

        self._native = leg(_x_api_key)
        self._compat = leg(bearer)

    def credential_ok(self) -> bool:
        return self._native.credential_ok()

    def call(self, call: Call) -> Body | SSE:
        leg = self._native if call.wire is Wire.MESSAGES else self._compat
        return leg.post(_PATHS[call.wire], call.body, retries=call.retries)
