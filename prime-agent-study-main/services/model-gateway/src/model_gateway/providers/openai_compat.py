"""OpenAI-compat upstream forwarder (keys stay in the gateway process)."""

from __future__ import annotations

from typing import Any

import httpx


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        upstream_model: str,
        stream: bool,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured on the gateway")
        if stream:
            raise NotImplementedError("openai provider stream not in MVP")
        body: dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
