"""Size limits on what memory takes in, and NUL characters refused with a 422."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from supervisor.memory.binding import MemoryBinding
from supervisor.memory.disclosure import Audience
from supervisor.memory.service import NotebookMemory
from supervisor.memory.tools import MAX_FACTS_PER_ADD, TurnMemory
from supervisor.memory_service import limits
from supervisor.memory_service.notebooks import ops as notebook_ops
from supervisor.memory_service.notebooks.errors import MemoryLimitExceeded
from supervisor.memory_service.notebooks.types import Edit
from supervisor.seams.services.memory_service_client import memory_service_credential
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

KEY = {"tenant_id": "t1", "owner": "alice", "agent_pin": "pin-1", "name": "default"}
SCOPE = {"tenant": "t1", "subject": "user:alice", "namespace": "agent"}


@pytest_asyncio.fixture
async def svc(url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", "memory-limits-tests-service-jwt-secret!!")
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with memory_service(url) as handle:
        yield handle


def _http(svc: MemoryServiceHandle) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),
        base_url="http://memory.test",
        headers={"Authorization": f"Bearer {memory_service_credential('t1')}"},
    )


async def test_a_capture_too_large_is_refused_whole(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    with pytest.raises(MemoryLimitExceeded, match="facts in one capture"):
        await client.capture(nb.id, [f"fact {i}" for i in range(limits.MAX_FACTS_PER_CAPTURE + 1)])
    with pytest.raises(MemoryLimitExceeded, match="characters in a fact"):
        await client.capture(nb.id, ["ok", "y" * (limits.MAX_FACT_CHARS + 1)])
    assert await client.facts(nb.id) == [], "nothing of a refused capture is kept"
    await client.capture(nb.id, ["kept"])
    fact = (await client.facts(nb.id))[0]
    with pytest.raises(MemoryLimitExceeded, match="characters in a fact"):
        await client.edit(nb.id, [Edit(fact.id, "z" * (limits.MAX_FACT_CHARS + 1))])


async def test_a_full_notebook_takes_no_new_fact_but_repeats_are_fine(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notebook_ops, "MAX_FACTS_PER_NOTEBOOK", 3)
    client = svc.client()
    nb = await client.ensure(**KEY)
    assert await client.capture(nb.id, ["a", "b", "c"]) == 3
    assert await client.capture(nb.id, ["A"]) == 0, "a repeat adds nothing, so it is allowed"
    with pytest.raises(MemoryLimitExceeded, match="facts in a notebook"):
        await client.capture(nb.id, ["d"])
    assert len(await client.facts(nb.id)) == 3


async def test_nul_is_a_422_everywhere_and_an_escaped_backslash_is_not_nul(svc: MemoryServiceHandle) -> None:
    nb = await svc.client().ensure(**KEY)
    async with _http(svc) as http:
        refused = [
            await http.post("/v1/memory/items", json={"scope": SCOPE, "content": "before\u0000after"}),
            await http.post(f"/v1/notebooks/{nb.id}/facts:capture", json={"facts": ["x\u0000y"]}),
        ]
        assert [r.status_code for r in refused] == [422, 422]
        assert all(r.json()["detail"]["code"] == "NUL_CHARACTER" for r in refused)
        literal = await http.post(f"/v1/notebooks/{nb.id}/facts:capture", json={"facts": ["the text \\u0000"]})
        assert literal.status_code == 200, literal.text


async def test_bank_items_and_recall_are_bounded(svc: MemoryServiceHandle) -> None:
    async with _http(svc) as http:
        too_long = {"scope": SCOPE, "content": "x" * (limits.MAX_ITEM_CHARS + 1)}
        assert (await http.post("/v1/memory/items", json=too_long)).status_code == 422
        assert (await http.post("/v1/memory/items", json={"scope": SCOPE, "content": ""})).status_code == 422
        for confidence in (-5, 1.5):
            body = {"scope": SCOPE, "content": "c", "confidence": confidence}
            assert (await http.post("/v1/memory/items", json=body)).status_code == 422
        for max_tokens in (0, -100, 10**9):
            body = {"scope": SCOPE, "query": "q", "max_tokens": max_tokens}
            assert (await http.post("/v1/memory/recall", json=body)).status_code == 422
        ok = await http.post("/v1/memory/items", json={"scope": SCOPE, "content": "fine", "confidence": 1})
        assert ok.status_code == 201, ok.text


def test_has_nul_reads_json_escapes() -> None:
    assert limits.has_nul(b'{"a": "\\u0000"}')
    assert limits.has_nul(b'{"a": "\\\\\\u0000"}'), "an escaped backslash, then a NUL"
    assert not limits.has_nul(b'{"a": "\\\\u0000"}'), "an escaped backslash, then the text u0000"
    assert not limits.has_nul(b'{"a": "plain"}')


async def test_the_memory_tool_caps_adds_and_reads_any_limit(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    binding = MemoryBinding(owner="alice", agent_pin="pin-1", write=nb.id, read=(nb.id,))
    tools = TurnMemory(NotebookMemory(client), binding, Audience.private("alice"), "s1", dependencies=())
    many = [{"text": f"auto fact {i}"} for i in range(MAX_FACTS_PER_ADD + 1)]
    refused = await tools.call("memory", {"action": "add", "facts": many})
    assert refused.is_error and f"at most {MAX_FACTS_PER_ADD}" in refused.text
    assert await client.facts(nb.id) == []
    long_fact = await tools.call("memory", {"action": "add", "facts": [{"text": "y" * 5000}]})
    assert long_fact.is_error and "characters in a fact" in long_fact.text
    await tools.call("memory", {"action": "add", "facts": [{"text": "prefers tea"}]})
    for limit in ("ten", None, -4, 10**6):
        found = await tools.call("memory", {"action": "search", "query": "tea", "limit": limit})
        assert not found.is_error and "prefers tea" in found.text
