"""Model calls a session makes beside its runs' own steps count toward its cost cap."""

from __future__ import annotations

from typing import Any

import pytest

from supervisor.memory.platform import gateway_one_shot
from supervisor.seams.core.types import ChatMessage, Completion, SubagentRequest
from supervisor.seams.providers.subagent.nested_session import NestedSessionSubagentProvider

USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "cost_total": 0.004}


class _Gateway:
    def __init__(self, usage: dict[str, Any] | None = USAGE) -> None:
        self._usage = usage

    def complete(self, messages: list[ChatMessage], tools: list[Any], **kwargs: Any) -> Completion:
        return Completion(ChatMessage("assistant", "an answer"), usage=self._usage)


def test_a_completion_says_what_it_cost() -> None:
    assert Completion(ChatMessage("assistant", ""), usage=USAGE).spent == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cost": 0.004,
    }
    assert Completion(ChatMessage("assistant", "")).spent == {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}


@pytest.mark.asyncio
async def test_memorys_one_shot_charges_its_cost() -> None:
    charged: list[float] = []

    async def charge(cost: float) -> None:
        charged.append(cost)

    answer = await gateway_one_shot(_Gateway(), charge=charge)("Classify sensitivity", "a fact")
    assert answer == "an answer" and charged == [0.004]


@pytest.mark.asyncio
async def test_a_free_one_shot_charges_nothing() -> None:
    charged: list[float] = []

    async def charge(cost: float) -> None:
        charged.append(cost)

    await gateway_one_shot(_Gateway(usage=None), charge=charge)("Classify sensitivity", "a fact")
    assert charged == []


@pytest.mark.parametrize("start", ["start_one_shot", "start_fork"])
def test_a_nested_subagent_reports_what_its_call_cost(start: str) -> None:
    result = getattr(NestedSessionSubagentProvider(_Gateway()), start)(SubagentRequest(prompt="do it"))
    assert result.text == "an answer"
    assert result.spent == {"input_tokens": 120, "output_tokens": 30, "cost": 0.004}
