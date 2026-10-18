"""
LLM call activity with retry and circuit breaker.

This activity handles all LLM API calls with:
- Automatic retry with exponential backoff
- Circuit breaker for failing providers
- Timeout management
- Token tracking
- Structured error handling

The activity delegates to the ModelGateway seam, allowing
different LLM providers (OpenAI, Anthropic, local) to be used.

Error Handling:
    - Retryable: RateLimitError, TimeoutError, ServerError
    - Non-retryable: AuthenticationError, InvalidRequestError
    - Circuit opens after repeated failures

Example:
    ```python
    result = await workflow.execute_activity(
        "call_llm",
        args=[session_id, messages, model_id, tools],
        start_to_close_timeout=timedelta(seconds=120),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    ```
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)

# Injected at runtime
_model_gateway: Any = None
_circuit_breaker: Any = None


class LLMError(ApplicationError):
    """Base error for LLM operations."""

    def __init__(self, message: str, error_type: str, retryable: bool = True):
        super().__init__(
            message,
            type=error_type,
            non_retryable=not retryable,
        )


class RateLimitError(LLMError):
    """Rate limit exceeded - retryable with backoff."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message, "RateLimitError", retryable=True)
        self.retry_after = retry_after


class InvalidRequestError(LLMError):
    """Invalid request - not retryable."""

    def __init__(self, message: str):
        super().__init__(message, "InvalidRequestError", retryable=False)


class AuthenticationError(LLMError):
    """Authentication failed - not retryable."""

    def __init__(self, message: str):
        super().__init__(message, "AuthenticationError", retryable=False)


class ModelUnavailableError(LLMError):
    """Model temporarily unavailable - retryable."""

    def __init__(self, message: str):
        super().__init__(message, "ModelUnavailableError", retryable=True)


def configure_llm_activity(
    model_gateway: Any,
    circuit_breaker: Any | None = None,
) -> None:
    """
    Configure LLM activity with dependencies.

    Args:
        model_gateway: ModelGatewayClient implementation.
        circuit_breaker: Optional CircuitBreaker for fault tolerance.
    """
    global _model_gateway, _circuit_breaker
    _model_gateway = model_gateway
    _circuit_breaker = circuit_breaker


def _get_gateway() -> Any:
    """Get configured gateway or raise."""
    if _model_gateway is None:
        raise RuntimeError(
            "Model gateway not configured. "
            "Call configure_llm_activity() first."
        )
    return _model_gateway


@activity.defn
async def call_llm(
    session_id: str,
    messages: list[dict[str, Any]],
    model_id: str,
    tools_enabled: list[str],
) -> dict[str, Any]:
    """
    Call LLM via model gateway.

    Handles tool-enabled completions with automatic retry.

    Args:
        session_id: Session for context/logging.
        messages: Conversation history (OpenAI format).
        model_id: Model identifier (e.g., "gpt-4", "claude-3-opus").
        tools_enabled: List of tool names to enable.

    Returns:
        Completion result with message, tool_calls, usage.

    Raises:
        RateLimitError: If rate limited (retryable).
        InvalidRequestError: If request is invalid (not retryable).
        AuthenticationError: If auth fails (not retryable).
        ModelUnavailableError: If model unavailable (retryable).
    """
    gateway = _get_gateway()
    info = activity.info()

    logger.info(
        f"Calling LLM: session={session_id}, model={model_id}, "
        f"messages={len(messages)}, tools={len(tools_enabled)}, "
        f"attempt={info.attempt}"
    )

    start_time = time.time()

    try:
        # Check circuit breaker
        if _circuit_breaker and not _circuit_breaker.allow_request():
            raise ModelUnavailableError(
                f"Circuit breaker open for model gateway. "
                f"Retry in {_circuit_breaker.time_until_retry():.1f}s"
            )

        # Build request
        request = {
            "model": model_id,
            "messages": messages,
            "tools": _build_tools_spec(tools_enabled) if tools_enabled else None,
            "tool_choice": "auto" if tools_enabled else None,
        }

        # Call gateway
        response = await gateway.complete(**request)

        # Record success
        if _circuit_breaker:
            await _circuit_breaker.record_success()

        # Parse response
        result = _parse_completion(response)

        duration = time.time() - start_time
        logger.info(
            f"LLM call completed: model={model_id}, "
            f"tokens={result['usage'].get('total_tokens')}, "
            f"tool_calls={len(result.get('tool_calls', []))}, "
            f"duration={duration:.2f}s"
        )

        return result

    except Exception as e:
        # Record failure
        if _circuit_breaker:
            await _circuit_breaker.record_failure(e)

        # Map to Temporal error types
        error = _map_exception(e)

        logger.warning(
            f"LLM call failed: model={model_id}, "
            f"error={type(error).__name__}: {error}"
        )

        raise error from e


def _build_tools_spec(tools_enabled: list[str]) -> list[dict[str, Any]]:
    """
    Build tools specification for LLM.

    In production, this would load tool schemas from a registry.
    For now, returns a placeholder structure.
    """
    # TODO: Load from tool registry
    return [
        {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Execute {tool_name} tool",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
        for tool_name in tools_enabled
    ]


def _parse_completion(response: Any) -> dict[str, Any]:
    """
    Parse gateway response to standard format.

    Handles both OpenAI and Anthropic response formats.
    """
    # Handle dict-like responses
    if isinstance(response, dict):
        message = response.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        usage = response.get("usage", {})

        return {
            "message": message,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

    # Handle object responses (pydantic models, etc.)
    if hasattr(response, "choices"):
        message = response.choices[0].message
        tool_calls = []

        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        usage = response.usage if hasattr(response, "usage") else None

        return {
            "message": {
                "role": "assistant",
                "content": message.content,
                "tool_calls": tool_calls if tool_calls else None,
            },
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
        }

    raise ValueError(f"Unknown response format: {type(response)}")


def _map_exception(e: Exception) -> LLMError:
    """Map gateway exceptions to Temporal error types."""
    error_str = str(e).lower()
    error_type = type(e).__name__

    # Rate limiting
    if "rate" in error_str or "429" in str(e) or error_type == "RateLimitError":
        retry_after = None
        if hasattr(e, "retry_after"):
            retry_after = e.retry_after
        return RateLimitError(str(e), retry_after)

    # Authentication
    if "auth" in error_str or "401" in str(e) or "403" in str(e):
        return AuthenticationError(str(e))

    # Invalid request
    if "invalid" in error_str or "400" in str(e) or "validation" in error_str:
        return InvalidRequestError(str(e))

    # Model unavailable
    if "unavailable" in error_str or "503" in str(e) or "502" in str(e):
        return ModelUnavailableError(str(e))

    # Default to retryable error
    return LLMError(str(e), "UnknownError", retryable=True)
