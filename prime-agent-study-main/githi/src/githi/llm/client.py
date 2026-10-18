"""Chat-completions client with tool calling, blocking or streaming.

`complete()` blocks and returns a finished message; `stream()` yields events
as bytes arrive. Both exist because they serve different readers: the agent
loop only ever needs the finished message, while a human watching a terminal
needs to see that something is happening. Streaming was pointless until githi
grew a UI -- without one it bought partial-chunk assembly and a second error
path in exchange for nothing.

The two paths converge deliberately: `stream()`'s terminal `done` event
carries exactly what `complete()` would have returned, so the loop behaves
identically whether or not anyone is watching.

Speaks the OpenAI chat-completions dialect, which every major provider and
gateway accepts, so one implementation covers all of them rather than one
adapter per vendor.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from ..errors import ConfigError, LLMError
from ..types import AssistantMessage, Message, ThinkingLevel, ToolCall, Usage
from .caching import apply_cache_breakpoints, supports_caching
from .streaming import MessageAssembler, StreamEvent, StreamEventType
from .usage import parse_usage

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_S = 120.0
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class Completion:
    message: AssistantMessage
    usage: Usage
    finish_reason: str = "stop"


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        http_client: httpx.Client | None = None,
        enable_caching: bool = True,
    ) -> None:
        if not api_key:
            raise ConfigError(
                "no API key. Set OPENROUTER_API_KEY (or pass api_key=) before running."
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.enable_caching = enable_caching
        self._client = http_client or httpx.Client(timeout=DEFAULT_TIMEOUT_S)
        # Parent + background child share one client; serialize provider calls.
        self._call_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def complete(self, messages: list[Message],
                 tools: list[dict[str, Any]],
                 *,
                 thinking_level: ThinkingLevel | None = None,
                 model: str | None = None) -> Completion:
        with self._call_lock:
            payload = self._payload(messages, tools, thinking_level=thinking_level,
                                    model=model)
            data = self._post(payload)
            return _parse_completion(data)

    def stream(self, messages: list[Message], tools: list[dict[str, Any]],
               interrupt: Any = None,
               *,
               thinking_level: ThinkingLevel | None = None,
               model: str | None = None) -> Iterator[StreamEvent]:
        """Yield events as the response arrives.

        `interrupt` is anything with `is_set()`. It is checked between chunks,
        so ESC stops generation within one chunk rather than at the end of the
        response -- and the partial message is still assembled and returned,
        because half an answer is information and discarding it wastes what
        the user already paid for.
        """
        with self._call_lock:
            yield from self._stream_locked(
                messages, tools, interrupt,
                thinking_level=thinking_level, model=model)

    def _stream_locked(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        interrupt: Any,
        *,
        thinking_level: ThinkingLevel | None,
        model: str | None,
    ) -> Iterator[StreamEvent]:
        payload = self._payload(messages, tools, thinking_level=thinking_level,
                                model=model)
        payload["stream"] = True
        assembler = MessageAssembler()

        try:
            for chunk in self._post_stream(payload):
                if interrupt is not None and interrupt.is_set():
                    assembler.finish_reason = "aborted"
                    break
                yield from assembler.feed(chunk)
        except LLMError as exc:
            yield StreamEvent(StreamEventType.ERROR, error=str(exc),
                              finish_reason="error")
            assembler.finish_reason = "error"

        yield from assembler.finish()

    def _payload(self, messages: list[Message],
                 tools: list[dict[str, Any]],
                 *,
                 thinking_level: ThinkingLevel | None = None,
                 model: str | None = None) -> dict[str, Any]:
        wire = [m.to_wire() for m in messages]
        tools_chars = len(json.dumps(tools)) if tools else 0
        active_model = model or self.model

        if self.enable_caching and supports_caching(active_model):
            wire = apply_cache_breakpoints(wire, tools_chars=tools_chars)

        payload: dict[str, Any] = {
            "model": active_model,
            "messages": wire,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
        # OpenRouter's normalised reasoning control (pi's thinkingFormat
        # "openrouter"). `off` omits the object entirely rather than sending
        # effort:"none", which some free models reject with a 400.
        if thinking_level is not None and thinking_level is not ThinkingLevel.OFF:
            payload["reasoning"] = {"effort": thinking_level.value}
        return payload

    def _post_stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Parse the SSE body into chunk dicts.

        No retry loop. A stream that fails mid-response has already delivered
        text the caller has rendered; restarting it would duplicate that text
        on screen, so a partial answer plus an error beats a silent retry.
        """
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json",
                   "Accept": "text/event-stream"}

        with self._client.stream("POST", f"{self.base_url}/chat/completions",
                                 json=payload, headers=headers) as response:
            if response.status_code >= 400:
                response.read()
                raise LLMError(f"provider error {response.status_code}: "
                               f"{_error_message(response)}")

            for line in response.iter_lines():
                line = line.strip()
                # Comments (": keep-alive") and blank separators carry no data.
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    # One malformed frame must not discard a response that is
                    # otherwise fine: the text around it is still real.
                    continue

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        last_error = ""

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(f"{self.base_url}/chat/completions",
                                             json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise LLMError(f"provider returned non-JSON: {exc}") from exc

                last_error = _error_message(response)
                # A 4xx that is not a rate limit means the request itself is
                # wrong; retrying spends money to receive the same answer.
                if response.status_code not in _RETRYABLE_STATUS:
                    raise LLMError(f"provider error {response.status_code}: {last_error}")

            if attempt < self.max_retries:
                time.sleep(self.retry_base_delay * (2 ** attempt))

        raise LLMError(f"provider call failed after {self.max_retries + 1} attempts: "
                       f"{last_error}")


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or body)[:300]


def _parse_completion(data: dict[str, Any]) -> Completion:
    try:
        choice = data["choices"][0]
        raw_message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}") from exc

    calls = [ToolCall.from_wire(c) for c in raw_message.get("tool_calls") or []]
    thinking = ""
    thinking_signature = ""
    for field in ("reasoning_content", "reasoning", "reasoning_text"):
        value = raw_message.get(field)
        if isinstance(value, str) and value:
            thinking = value
            thinking_signature = field
            break
    message = AssistantMessage(
        content=raw_message.get("content") or "", tool_calls=calls,
        thinking=thinking, thinking_signature=thinking_signature)
    return Completion(message=message, usage=parse_usage(data.get("usage")),
                      finish_reason=choice.get("finish_reason") or "stop")
