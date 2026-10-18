"""HTTP ModelGatewayClient — talks to sibling model-gateway over OpenAI-compat."""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from supervisor.obs.trace_context import current_outbound_traceparent
from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent


def _map_tool_calls(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    return [c for c in raw if isinstance(c, dict)]


class HttpModelGatewayClient:
    """Provider: OpenAI-compat HTTP adapter (G-1, G-4).

    Couples only via ``base_url`` (+ optional bearer). Does not import the
    gateway package — developers of either side can work independently.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        default_model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url required")
        self._base = base_url.rstrip("/")
        self._token = token
        self._default_model = default_model
        self._client = httpx.Client(timeout=timeout)
        self._closed = False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # D13 B / T6: propagate W3C traceparent so gateway child spans share the run.
        tp = current_outbound_traceparent()
        if tp:
            headers["traceparent"] = tp
        else:
            try:
                from opentelemetry.propagate import inject

                inject(headers)
            except Exception:
                pass
        return headers

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [m.to_wire() for m in messages],
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        resp = self._client.post(
            f"{self._base}/v1/chat/completions",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"model gateway error {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        tool_calls = _map_tool_calls(msg.get("tool_calls"))
        message = ChatMessage(
            role=msg.get("role", "assistant"),
            content=msg.get("content") or "",
            tool_calls=tool_calls,
        )
        return Completion(
            message=message,
            model=data.get("model") or model or self._default_model,
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
            tool_calls=tool_calls,
        )

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [m.to_wire() for m in messages],
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        with self._client.stream(
            "POST",
            f"{self._base}/v1/chat/completions",
            json=body,
            headers=self._headers(),
        ) as resp:
            if resp.status_code >= 400:
                # Read error body for the message (stream response).
                err = resp.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"model gateway error {resp.status_code}: {err}")
            finish: str | None = None
            stream_model = model or self._default_model
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if not text.startswith("data:"):
                    continue
                payload = text[5:].strip()
                if not payload or payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and chunk.get("model"):
                    stream_model = chunk["model"]
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not choices:
                    continue
                choice0 = choices[0] if isinstance(choices, list) else None
                if not isinstance(choice0, dict):
                    continue
                delta = choice0.get("delta") or {}
                content = delta.get("content") if isinstance(delta, dict) else None
                if content:
                    yield StreamEvent(type="delta", delta=str(content), model=stream_model)
                fr = choice0.get("finish_reason")
                if fr:
                    finish = str(fr)
            yield StreamEvent(
                type="done",
                delta="",
                finish_reason=finish or "stop",
                model=stream_model,
            )

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True
