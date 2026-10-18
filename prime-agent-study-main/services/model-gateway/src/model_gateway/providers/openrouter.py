"""OpenRouter ChatProvider — OpenAI-compat forwarder (keys stay in-gateway)."""

from __future__ import annotations

from model_gateway.providers.openai_compat import OpenAICompatProvider


class OpenRouterChatProvider(OpenAICompatProvider):
    """Upstream: https://openrouter.ai/api/v1/chat/completions"""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        http_referer: str = "https://yal.local",
        app_title: str = "yal-model-gateway",
        transport=None,
        retries: int = 1,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            transport=transport,
            extra_headers={
                "HTTP-Referer": http_referer,
                "X-Title": app_title,
            },
            retries=retries,
            key_env_name="OPENROUTER_API_KEY",
        )
