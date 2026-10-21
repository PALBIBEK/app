"""What every wire transport shares: the call, the result, SSE, retries and errors.

Every transport streams (``stream: true``) and assembles the finished assistant
turn from the provider's events, as pi-ai does — long generations never sit
behind an idle connection, and a turn's native blocks are rebuilt exactly.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from supervisor.models.messages import AssistantBlock, Context
from supervisor.models.spec import Cost, ModelSpec, ThinkingLevel, TokenUsage
from supervisor.seams.core.types import invalid_arguments

type Body = dict[str, Any]
type CacheRetention = Literal["none", "short", "long"]
type StopReason = Literal["stop", "length", "toolUse"]
type PayloadHook = Callable[[Body], Body]

DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY_S = 60.0
_RETRYABLE_STATUS = frozenset({408, 409, 429})


@dataclass(frozen=True, slots=True)
class RequestOptions:
    session_id: str | None = None
    """Prompt-cache key and gateway session affinity."""
    thinking: ThinkingLevel = "off"
    """Already clamped to the model's supported levels."""
    max_tokens: int | None = None
    temperature: float | None = None
    cache_retention: CacheRetention = "short"
    tool_choice: Any = None
    service_tier: str | None = None
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_s: float = 600.0
    payload_hook: PayloadHook | None = None
    """Last edit to the request body before it is sent (wire model id, request overrides)."""


@dataclass(frozen=True, slots=True)
class WireCall:
    spec: ModelSpec
    context: Context
    api_key: str
    http: httpx.Client
    headers: Mapping[str, str] = field(default_factory=dict)
    """Extra headers (the gateway key header), applied last."""
    options: RequestOptions = RequestOptions()


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    blocks: tuple[AssistantBlock, ...]
    usage: TokenUsage
    cost: Cost
    stop_reason: StopReason
    response_id: str | None = None
    service_tier: str | None = None


BUDGET_EXHAUSTED = "VIRTUAL_KEY_BUDGET_EXCEEDED"


class ModelCallError(Exception):
    """The provider (or the gateway) refused or failed the call."""

    def __init__(
        self, message: str, *, status: int | None = None, error_type: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type

    @property
    def code(self) -> str | None:
        return self.error_type

    @property
    def retryable(self) -> bool:
        """Whether trying the call again later could succeed (a spent budget never will)."""
        if self.error_type == BUDGET_EXHAUSTED:
            return False
        status = self.status
        return status is None or status in _RETRYABLE_STATUS or status >= 500


class Transport(Protocol):
    def __call__(self, call: WireCall) -> AssistantTurn: ...


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str | None
    data: str


def iter_sse(lines: Iterator[str]) -> Iterator[SSEEvent]:
    """Server-sent events from decoded lines (comments skipped, multi-line data joined)."""
    event: str | None = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if event is not None or data:
                yield SSEEvent(event, "\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if event is not None or data:
        yield SSEEvent(event, "\n".join(data))


def stream_events(
    call: WireCall, url: str, headers: Mapping[str, str], body: Body
) -> Iterator[SSEEvent]:
    """POST ``body`` and yield its event stream, retrying transient failures first.

    Retries follow the OpenAI/Anthropic SDK policy: ``x-should-retry`` wins, then
    408/409/429/5xx and connection errors; delay from ``retry-after-ms`` /
    ``retry-after``, else jittered exponential backoff (0.5 s doubling, 8 s cap). A
    server asking to wait longer than a minute fails the call instead, and so does
    a spent gateway budget (a 429 no wait can fix).
    """
    if call.options.payload_hook is not None:
        body = call.options.payload_hook(body)
    content = json.dumps(body, ensure_ascii=False).encode("utf-8", errors="replace")
    request_headers = {"content-type": "application/json", "accept": "text/event-stream", **headers}
    retries_left = max(0, call.options.max_retries)
    attempt = 0
    while True:
        request = call.http.build_request(
            "POST", url, content=content, headers=request_headers, timeout=call.options.timeout_s
        )
        try:
            response = call.http.send(request, stream=True)
        except httpx.TransportError as exc:
            if retries_left > 0:
                retries_left -= 1
                time.sleep(_backoff(attempt))
                attempt += 1
                continue
            raise ModelCallError(f"model endpoint unreachable: {type(exc).__name__}") from exc
        if response.is_success:
            break
        error = _error(response)
        response.close()
        if retries_left > 0 and error.error_type != BUDGET_EXHAUSTED and _retryable(response):
            delay = _server_delay(response, error) or _backoff(attempt)
            retries_left -= 1
            attempt += 1
            time.sleep(delay)
            continue
        raise error
    try:
        yield from iter_sse(response.iter_lines())
    finally:
        response.close()


def _retryable(response: httpx.Response) -> bool:
    should: str | None = response.headers.get("x-should-retry")
    if should in ("true", "false"):
        return should == "true"
    status: int = response.status_code
    return status in _RETRYABLE_STATUS or status >= 500


def _backoff(attempt: int) -> float:
    return min(0.5 * 2.0**attempt, 8.0) * (1 - random.random() * 0.25)


def _server_delay(response: httpx.Response, error: ModelCallError) -> float | None:
    raw_ms = response.headers.get("retry-after-ms")
    raw_s = response.headers.get("retry-after")
    delay: float | None = None
    try:
        if raw_ms:
            delay = float(raw_ms) / 1000
        elif raw_s:
            delay = float(raw_s)
    except ValueError:
        return None
    if delay is not None and delay > MAX_RETRY_DELAY_S:
        raise ModelCallError(
            f"Server requested {int(delay)}s retry delay (max: {int(MAX_RETRY_DELAY_S)}s). {error}",
            status=error.status,
            error_type=error.error_type,
        )
    return delay


def _error(response: httpx.Response) -> ModelCallError:
    raw = response.read()
    message, error_type = raw[:500].decode("utf-8", errors="replace") or "no detail", None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        detail = parsed.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            error = detail["error"]
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or message)
            error_type = str(error.get("type") or error.get("code") or "") or None
        elif isinstance(error, str):
            message = error
    return ModelCallError(
        f"{response.status_code} {message}", status=response.status_code, error_type=error_type
    )


def parse_json(data: str, what: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ModelCallError(f"Could not parse {what} event: {exc.msg}") from exc


def parse_partial_json(raw: str) -> dict[str, Any]:
    """Tool arguments from a finished stream.

    An unparseable buffer (or a non-object) is marked, not emptied: run with ``{}``
    the tool would fail with a misleading "missing field", or worse, run with its
    defaults. The loop refuses a marked call and tells the model to resend it.
    """
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return invalid_arguments(raw)
    return value if isinstance(value, dict) else invalid_arguments(raw)


def short_hash(text: str) -> str:
    """pi-ai's ``shortHash`` (cyrb53 halves in base 36), for ids that must stay stable."""

    def imul(a: int, b: int) -> int:
        return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF

    h1, h2 = 0xDEADBEEF, 0x41C6CE57
    for code in _utf16_units(text):
        h1 = imul(h1 ^ code, 2654435761)
        h2 = imul(h2 ^ code, 1597334677)
    h1 = imul(h1 ^ (h1 >> 16), 2246822507) ^ imul(h2 ^ (h2 >> 13), 3266489909)
    h2 = imul(h2 ^ (h2 >> 16), 2246822507) ^ imul(h1 ^ (h1 >> 13), 3266489909)
    return _base36(h2 & 0xFFFFFFFF) + _base36(h1 & 0xFFFFFFFF)


def _utf16_units(text: str) -> Iterator[int]:
    raw = text.encode("utf-16-le", errors="surrogatepass")
    for i in range(0, len(raw), 2):
        yield raw[i] | (raw[i + 1] << 8)


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    return out


def merged_headers(*sources: Mapping[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in sources:
        if source:
            merged.update(source)
    return merged
