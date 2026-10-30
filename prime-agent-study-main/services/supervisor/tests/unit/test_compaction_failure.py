"""A compaction whose summary fails never drops the conversation (Pi Durable's compaction).

A failure that may pass is asked again after a wait; one that will not, or one out of
retries, fails the compaction and the conversation goes on whole.
"""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
from typing import Any

import pytest

from supervisor.agent.events import CompactionEnd, MessageEnd
from supervisor.agent.runtimes.githi.classify import RetrySpec, is_retryable
from supervisor.seams.compaction import SummaryFailed
from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    CompactionResult,
)
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.memory.basic_compaction import BasicCompactionProvider
from supervisor.temporal.context_memory import PinnedContextMemory
from supervisor.temporal.steps import conversation

FAST = RetrySpec(max_retries=3, base_delay_s=0.01, max_delay_s=0.05)
RATE_LIMITED = RuntimeError("model gateway error 429: rate limited")
REFUSED = RuntimeError("model gateway error 401: invalid api key")
SUMMARY = CompactionDecision(
    action="custom",
    result=CompactionResult(summary="## Goal\nship it", first_kept_entry_id="2", tokens_before=9, tokens_after=3),
    spent={"input_tokens": 100, "output_tokens": 20, "cost": 0.002},
)


def _binding(*outcomes: Exception | CompactionDecision) -> tuple[Any, list[object]]:
    """A pinned binding whose provider meets ``outcomes`` in turn; the attempts it saw."""
    calls: list[object] = []
    left = list(outcomes)

    def before_compact(context: Any, preparation: Any, reason: str) -> CompactionDecision:
        outcome = left.pop(0)
        calls.append(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    memory = SimpleNamespace(before_compact=before_compact, estimate_tokens=lambda messages: 10)
    return SimpleNamespace(memory=memory, context=None), calls


async def _summarise(binding: Any, retries: RetrySpec = FAST, deadline: float = math.inf) -> Any:
    return await PinnedContextMemory._summarise(  # type: ignore[arg-type]
        "cmp-1", binding, None, [], retries=retries, deadline=deadline
    )


@pytest.mark.asyncio
async def test_a_rate_limited_summary_is_asked_again() -> None:
    binding, calls = _binding(RATE_LIMITED, RATE_LIMITED, SUMMARY)
    end = await _summarise(binding)
    assert len(calls) == 3
    assert end.stop_reason == "ok" and end.error is None
    assert end.messages[0]["content"].startswith("## Goal")


@pytest.mark.asyncio
async def test_a_refused_summary_fails_the_compaction_at_once_and_keeps_the_conversation() -> None:
    binding, calls = _binding(REFUSED, SUMMARY)
    end = await _summarise(binding)
    assert len(calls) == 1, "a refusal is not asked again"
    assert end.stop_reason == "error" and end.messages is None and end.summary_digest is None
    assert "invalid api key" in end.error and "kept whole" in end.error


@pytest.mark.asyncio
async def test_a_summary_out_of_retries_fails_the_compaction() -> None:
    binding, calls = _binding(*[RATE_LIMITED] * 4, SUMMARY)
    end = await _summarise(binding)
    assert len(calls) == 4, "the first call and three retries"
    assert end.stop_reason == "error" and end.messages is None
    assert "after 4 attempts" in end.error


@pytest.mark.asyncio
async def test_failed_attempts_still_count_what_they_spent() -> None:
    try:
        raise RuntimeError("model gateway error 503: overloaded")
    except RuntimeError as cause:
        failed = SummaryFailed(str(cause), spent={"input_tokens": 40, "output_tokens": 0, "cost": 0.001})
        failed.__cause__ = cause
    binding, calls = _binding(failed, SUMMARY)
    end = await _summarise(binding)
    assert len(calls) == 2, "asked again: its cause (a 503) may pass"
    assert end.stop_reason == "ok"
    assert (end.usage.input_tokens, end.usage.output_tokens) == (140, 20)
    assert end.usage.cost_total == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_a_wait_that_would_outlast_the_step_is_not_taken() -> None:
    binding, calls = _binding(RATE_LIMITED, SUMMARY)
    end = await _summarise(binding, deadline=time.monotonic())  # the step has no time left
    assert len(calls) == 1 and end.stop_reason == "error"


class _Failing(FakeModelGatewayClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error


def _preparation(previous: str | None) -> CompactionPreparation:
    return CompactionPreparation(
        first_kept_entry_id="2",
        messages_to_summarize=[
            {"role": "user", "content": "build the payment service"},
            {"role": "assistant", "content": "done: wrote pay.py"},
        ],
        tokens_before=50_000,
        previous_summary=previous,
        context_window=128_000,
        reserve_tokens=16_000,
    )


@pytest.mark.parametrize("previous", [None, "Earlier: the user wants a payment service in pay.py."])
def test_the_basic_provider_raises_rather_than_replace_history_with_a_notice(previous: str | None) -> None:
    """It used to answer a failed summary with the plain notice: the span (and, with an
    earlier summary, that summary too) was dropped for good."""
    provider = BasicCompactionProvider(_Failing(RATE_LIMITED))
    with pytest.raises(SummaryFailed, match="429") as raised:
        provider.before_compact(None, _preparation(previous), reason="threshold")  # type: ignore[arg-type]
    assert raised.value.__cause__ is RATE_LIMITED  # which says whether to ask again
    assert raised.value.spent == {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}


def test_an_empty_summary_is_a_failure_that_is_not_asked_again() -> None:
    class _Silent(FakeModelGatewayClient):
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(message=SimpleNamespace(content=""), usage={})

    with pytest.raises(SummaryFailed) as raised:
        BasicCompactionProvider(_Silent()).before_compact(None, _preparation(None), reason="threshold")  # type: ignore[arg-type]
    assert raised.value.__cause__ is None and not is_retryable(raised.value)


def test_a_failed_compaction_inside_a_run_leaves_the_conversation_as_it_was() -> None:
    prior = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "answer"}]
    run = [
        SimpleNamespace(payload=MessageEnd(role="user", content=[{"type": "text", "text": "now"}])),
        SimpleNamespace(payload=CompactionEnd(compaction_id="c", stop_reason="error", in_run=True, error="x")),
    ]
    rebuilt = conversation(prior, run)
    assert [m["role"] for m in rebuilt] == ["user", "assistant", "user"]
