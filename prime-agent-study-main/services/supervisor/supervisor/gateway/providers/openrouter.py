"""OpenRouter upstream: OpenAI-compatible chat completions with attribution headers."""

from __future__ import annotations

import httpx

from supervisor.gateway.providers.base import Wire
from supervisor.gateway.providers.openai_compat import OpenAICompatProvider


class OpenRouterChatProvider(OpenAICompatProvider):
    """Upstream: https://openrouter.ai/api/v1/chat/completions"""

    name = "openrouter"
    wires: frozenset[Wire] = frozenset({Wire.CHAT})

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        http_referer: str = "https://yal.local",
        app_title: str = "yal-model-gateway",
        transport: httpx.BaseTransport | None = None,
        retries: int = 1,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            transport=transport,
            extra_headers={"HTTP-Referer": http_referer, "X-Title": app_title},
            retries=retries,
            key_env_name="OPENROUTER_API_KEY",
        )
