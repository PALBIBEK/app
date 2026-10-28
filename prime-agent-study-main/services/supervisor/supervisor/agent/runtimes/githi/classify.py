"""githi's rules for one model step: the step limit, its notices, and which calls must not run.

Pure functions of the run's pinned ``LoopSpec``, the step number and the journaled reply,
so every place that applies them (the model step that plans the calls, the tool step that
answers them, a retried or re-homed activity, a replay) reaches the same decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from supervisor.agent.events import MessageEnd
from supervisor.harness import LoopSpec
from supervisor.seams.core.types import INVALID_ARGUMENTS_KEY

__all__ = [
    "DEFAULT_RETRY",
    "FINAL_STEP_NOTICE",
    "RetrySpec",
    "is_context_overflow",
    "is_final_step",
    "is_retryable",
    "loop_spec",
    "refusal",
    "retry_delay",
    "retry_spec",
    "steps_left_notice",
    "unique_call_ids",
]

FINAL_STEP_NOTICE: Final = (
    "This is the last step of this run: no more tool calls. Give your complete answer "
    "now from what you already know, and say plainly which parts are unverified."
)
"""Shown on the last step's request only (never journalled), with ``tool_choice=none``:
the run ends with an answer instead of at the limit with none."""


def steps_left_notice(left: int) -> str:
    """Shown on the requests near the limit only, so a stale count never persists."""
    return (
        f"[{left} steps left in this run. If you have enough to conclude, answer now; "
        "combine any last checks into a single call.]"
    )


def loop_spec(manifest: Any) -> LoopSpec:
    """The manifest's run-length settings; the defaults for manifests without them."""
    return getattr(manifest, "loop", None) or LoopSpec()


def is_final_step(spec: LoopSpec, step: int) -> bool:
    return spec.force_answer_on_final_step and spec.max_steps - step <= 1


def refusal(
    spec: LoopSpec, step: int, assistant: MessageEnd, call: dict[str, Any]
) -> tuple[str, str] | None:
    """Why a call the model made must not run, as (code, message for the model); None to run it."""
    if is_final_step(spec, step):
        return "STEP_LIMIT", (
            "not run: this run has used its last step. Answer with what you have; "
            "the user can ask you to continue."
        )
    if assistant.finish_reason == "length":
        return "TRUNCATED_TOOL_CALL", (
            "not run: your reply was cut off by the output limit, so this call's "
            "arguments may be incomplete. Send it again in a shorter reply."
        )
    if INVALID_ARGUMENTS_KEY in (call.get("arguments") or {}):
        return "MALFORMED_ARGUMENTS", (
            "not run: the arguments were not valid JSON and could not be parsed. "
            "Send the call again with well-formed JSON arguments."
        )
    return None


_OVERFLOW: Final = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "context_window_full",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "token limit",
    "reduce the length",
    "string too long",
)


def is_context_overflow(error: BaseException | str) -> bool:
    """Whether a model call failed because the request does not fit the model's window.

    Providers say so in their own words (pi's ``isContextOverflow`` keeps the same list),
    and the supervisor's own model layer raises ``ContextWindowFull`` before sending one.
    """
    if type(error).__name__ == "ContextWindowFull":
        return True
    text = str(error).lower()
    return any(marker in text for marker in _OVERFLOW)


# --- retrying a model call (pi's auto-retry) ----------------------------------------


@dataclass(frozen=True)
class RetrySpec:
    """How a failed model call is asked again: pi's defaults (3 retries, 2 s doubling).

    The wait is a durable timer in the workflow, so it survives a worker restart and
    holds no worker while it runs; a provider's ``Retry-After`` is honoured up to
    ``max_delay_s`` (pi's ``maxRetryDelayMs``): longer than that, the run ends saying so
    instead of hanging.
    """

    max_retries: int = 3
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0


DEFAULT_RETRY: Final = RetrySpec()

_RETRY_STATUS: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
_RETRY_TEXT: Final = (
    "overloaded",
    "rate limit",
    "rate_limit",
    "too many requests",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection",
    "network",
    "econnreset",
    "fetch failed",
    "try again",
)
_STATUS: Final = re.compile(r"model gateway error (\d{3})")


def _status(error: BaseException) -> int | None:
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status
    found = _STATUS.search(str(error))
    return int(found.group(1)) if found else None


def is_retryable(error: BaseException) -> bool:
    """Whether a failed model call may succeed if asked again unchanged.

    A rate limit, an overloaded or unavailable provider, or a dropped connection may
    pass; a refused request (bad key, unknown model, invalid body) or one that does
    not fit the window will fail the same way again (the window is compacted instead).
    """
    if is_context_overflow(error):
        return False
    if getattr(error, "retryable", None) is False:
        return False  # the model layer knows better (a spent gateway budget never refills)
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    status = _status(error)
    if status is not None:
        return status in _RETRY_STATUS
    text = str(error).lower()
    return any(marker in text for marker in _RETRY_TEXT)


def retry_spec(manifest: Any) -> RetrySpec:
    """The retry settings the manifest pins (its ``loop:`` block); pi's defaults without."""
    loop = getattr(manifest, "loop", None)
    if loop is None:
        return DEFAULT_RETRY
    pinned = RetrySpec(loop.model_retries, loop.retry_base_seconds, loop.retry_max_seconds)
    return DEFAULT_RETRY if pinned == RetrySpec() else pinned


def retry_delay(spec: RetrySpec, attempt: int, error: BaseException) -> float | None:
    """Seconds to wait before retry ``attempt`` (1-based); None: do not retry.

    The provider's ``Retry-After`` when it gave one, else ``base_delay_s`` doubling.
    None past ``max_retries``, or when the provider asks for longer than ``max_delay_s``.
    """
    if attempt > spec.max_retries:
        return None
    asked = getattr(error, "retry_after", None)
    if isinstance(asked, (int, float)):
        return float(asked) if asked <= spec.max_delay_s else None
    return float(min(spec.base_delay_s * 2 ** (attempt - 1), spec.max_delay_s))


def unique_call_ids(parts: list[dict[str, Any]], taken: set[str], step: int) -> list[dict[str, Any]]:
    """The reply's parts with every tool-call id unique in the run.

    Calls are journalled by id, so an id the run has seen already would be answered
    with the earlier call's result without running. Some models send no id, or the
    same one each step ("call_0"); those are renamed here, before the reply is
    journalled, so the model reads its calls back under the names it is answered by.
    """
    out: list[dict[str, Any]] = []
    used = set(taken)
    for n, part in enumerate(parts):
        if part.get("type") == "tool_call":
            raw = str(part.get("id") or "")
            if not raw or raw in used:
                part = {**part, "id": f"{raw or 'call'}~{step}.{n}"}
            used.add(str(part["id"]))
        out.append(part)
    return out
