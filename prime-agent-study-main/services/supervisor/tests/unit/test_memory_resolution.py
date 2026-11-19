"""Write-time resolution of new facts against held ones (after Graphiti and mem0)."""

from __future__ import annotations

import pytest

from supervisor.memory.resolution import RESOLUTION_PROMPT, Resolution, parse, related, resolve
from supervisor.memory_service.notebooks.render import dates, fact_line
from supervisor.memory_service.notebooks.types import Fact, Provenance, Sensitivity

DAY = 86_400_000


def _fact(i: int, text: str, *, created: int = 1_700_000_000_000, updated: int | None = None) -> Fact:
    own = Provenance(Sensitivity.ORDINARY, ("personal:alice",), False)
    return Fact(str(i), text, own, created, updated or created)


HELD = [_fact(1, "Prefers tea in the morning"), _fact(2, "Owns the billing service"), _fact(3, "Lives in Lisbon")]


def test_only_facts_sharing_content_words_are_compared() -> None:
    assert [f.id for f in related(HELD, ["Prefers coffee in the morning"])] == ["1"]
    assert [f.id for f in related(HELD, ["Moved to Porto", "Billing service moved to team b"])] == ["2"]
    assert related(HELD, ["Likes jazz"]) == []


def test_the_answer_establishes_what_changes_and_nothing_else() -> None:
    candidates = [HELD[0], HELD[1]]
    answer = '{"facts": [{"new": 0, "duplicates": [], "contradicts": [0]}, {"new": 1, "duplicates": [1], "contradicts": []}]}'
    assert parse(answer, 2, candidates) == Resolution(add=(0,), retire=("1",))
    noisy = 'Sure: {"facts": [{"new": 7, "contradicts": [0]}, {"new": 0, "contradicts": [9, true, "0"]}]}'
    assert parse(noisy, 1, candidates) == Resolution(add=(0,), retire=()), "ids outside the lists are ignored"
    assert parse("NONE", 1, candidates) is None
    assert parse('{"facts": "x"}', 1, candidates) is None


@pytest.mark.asyncio
async def test_resolve_asks_once_with_small_ids_and_dated_facts() -> None:
    asked: list[tuple[str, str]] = []

    async def one_shot(system: str, content: str) -> str:
        asked.append((system, content))
        return '{"facts": [{"new": 0, "duplicates": [], "contradicts": [0]}]}'

    got = await resolve(one_shot, ["Prefers coffee in the morning"], HELD)
    assert got == Resolution(add=(0,), retire=("1",))
    ((system, body),) = asked
    assert system == RESOLUTION_PROMPT
    assert body == "EXISTING FACTS:\n[0] (2023-11-14) Prefers tea in the morning\n\nNEW FACTS:\n[0] Prefers coffee in the morning"


@pytest.mark.asyncio
async def test_no_related_facts_no_call_and_a_failed_call_changes_nothing() -> None:
    async def never(system: str, content: str) -> str:
        raise AssertionError("not called without related facts")

    assert await resolve(never, ["Likes jazz"], HELD) == Resolution(add=(0,), retire=())

    async def broken(system: str, content: str) -> str:
        raise TimeoutError("gateway")

    assert await resolve(broken, ["Prefers coffee"], HELD) == Resolution(add=(0,), retire=())


def test_an_edited_fact_shows_when_it_last_changed() -> None:
    created = 1_600_000_000_000
    assert dates(_fact(1, "x", created=created)) == "2020-09-13"
    assert dates(_fact(1, "x", created=created, updated=created + 3_600_000)) == "2020-09-13", "same day"
    edited = _fact(1, "Prefers green tea", created=created, updated=created + 30 * DAY)
    assert fact_line(edited) == "#1 (2020-09-13, updated 2020-10-13) Prefers green tea"
