"""Tier 1 extraction asks a model (as mem0, Graphiti and LangMem do), with the same QM
prompt the supervisor uses for notebooks; without a model there is no extraction."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from supervisor.memory_service.extract import MAX_ATTEMPTS
from supervisor.memory_service.extraction_prompt import MEMORY_EXTRACTION_PROMPT
from supervisor.memory_service.model_extractor import ModelExtractor
from supervisor.memory_service.models import ExtractionJobRow, MemoryItemRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.tier2_types import TokenBudget
from supervisor.memory_service.types import Scope
from tests.support.memory_extraction import ScriptedModel

SC = Scope("t1", "user:alice", "agent")


async def _turn(svc: MemoryService, user: str, assistant: str = "Noted.") -> str:
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": user})
    svc.append(h.id, "assistant/message", {"text": assistant})
    svc.append(h.id, "turn/end", {})
    await svc.flush(h.id)
    return h.id


async def _items(svc: MemoryService) -> list[str]:
    async with svc._factory() as db:
        return sorted(r.content for r in await db.scalars(select(MemoryItemRow)))


@pytest.mark.asyncio
async def test_the_model_reads_the_exchange_and_states_the_facts(url: str) -> None:
    model = ScriptedModel("SENSITIVITY: ordinary\n- Prefers pytest for all tests")
    extractor = ModelExtractor(
        "http://model.test/v1/", model="m-1", token="secret-token", transport=httpx.MockTransport(model)
    )
    svc = MemoryService(database_url=url, extractor=extractor)
    await svc.startup()
    await _turn(svc, "I prefer pytest for all tests", "I'll use pytest.")
    assert await svc.process_extraction_jobs() == 1
    ((request),) = model.requests
    assert request["model"] == "m-1" and request["temperature"] == 0
    system, user = request["messages"]  # type: ignore[misc]
    assert system == {"role": "system", "content": MEMORY_EXTRACTION_PROMPT}
    assert user["content"] == "User said:\nI prefer pytest for all tests\n\nAssistant replied:\nI'll use pytest."
    assert await _items(svc) == ["Prefers pytest for all tests"]
    hit = await svc.recall(SC, "pytest", TokenBudget(max_tokens=64))
    assert [i.content for i in hit.items] == ["Prefers pytest for all tests"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_chatter_the_model_finds_nothing_in_is_not_remembered(url: str) -> None:
    svc = MemoryService(database_url=url, extractor=ScriptedModel("SENSITIVITY: ordinary\nNONE").extractor())
    await svc.startup()
    await _turn(svc, "I have no idea what you mean here")
    await _turn(svc, "I want to see the dashboard now")
    assert await svc.process_extraction_jobs() == 2
    assert await _items(svc) == [], "no pattern guesses: a model found nothing worth keeping"
    await svc.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["sensitive", "restricted", "unknown", None])
async def test_only_ordinary_facts_reach_the_bank(url: str, label: str | None) -> None:
    answer = (f"SENSITIVITY: {label}\n" if label else "") + "- Takes medication for asthma"
    svc = MemoryService(database_url=url, extractor=ScriptedModel(answer).extractor())
    await svc.startup()
    await _turn(svc, "I take medication for asthma")
    await svc.process_extraction_jobs()
    assert await _items(svc) == [], "the bank's recall applies no audience rules"
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_failing_model_call_is_retried(url: str) -> None:
    calls: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "upstream busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "SENSITIVITY: ordinary\n- Uses vim"}}]})

    extractor = ModelExtractor("http://model.test/v1", model="m", transport=httpx.MockTransport(flaky))
    svc = MemoryService(database_url=url, extractor=extractor)
    await svc.startup()
    await _turn(svc, "I use vim")
    await svc.process_extraction_jobs()
    async with svc._factory() as db, db.begin():
        (job,) = list(await db.scalars(select(ExtractionJobRow)))
        assert (job.status, job.attempts) == ("pending", 1) and "503" in (job.error or "")
        job.retry_at_ms = 0
    assert MAX_ATTEMPTS > 1
    await svc.process_extraction_jobs()
    assert await _items(svc) == ["Uses vim"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_without_a_model_nothing_is_queued(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MEMORY_EXTRACTION_URL", "MEMORY_EXTRACTION_MODEL", "MEMORY_EXTRACTION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    svc = MemoryService(database_url=url)
    await svc.startup()
    await _turn(svc, "I prefer tabs over spaces")
    async with svc._factory() as db:
        assert list(await db.scalars(select(ExtractionJobRow))) == []
    await svc.aclose()


def test_the_model_is_configured_from_the_environment() -> None:
    assert ModelExtractor.from_env({}) is None
    assert ModelExtractor.from_env({"MEMORY_EXTRACTION_URL": "http://gw:8090/v1"}) is None, "a model is needed too"
    configured = ModelExtractor.from_env(
        {"MEMORY_EXTRACTION_URL": "http://gw:8090/v1", "MEMORY_EXTRACTION_MODEL": "free-model", "MEMORY_EXTRACTION_TOKEN": "t"}
    )
    assert configured is not None
    assert (configured._url, configured._model, configured._headers) == (
        "http://gw:8090/v1/chat/completions",
        "free-model",
        {"Authorization": "Bearer t"},
    )
