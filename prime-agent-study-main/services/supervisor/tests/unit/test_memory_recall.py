"""Recall budgets, deltas, the audience checkpoint, history search and extraction."""

from __future__ import annotations

import pytest

from supervisor.agent.events import Envelope, MemoryContext, MemoryInjected, MessageEnd, ToolStart
from supervisor.memory import boundary, history
from supervisor.memory.binding import MemoryBinding, Strategy
from supervisor.memory.client import NotebookClient
from supervisor.memory.cross import Recalled, recall_across
from supervisor.memory.delta import Shown, held_hashes, recall_delta, shown_of
from supervisor.memory.disclosure import Audience
from supervisor.memory.extraction import Exchange, extract_facts, parse_facts
from supervisor.memory.service import CaptureContext, NotebookMemory
from supervisor.memory.tools import TurnMemory
from supervisor.memory.turn import dependencies, memory_section, recall_query
from supervisor.memory_service.notebooks.types import (
    UNTRACKED,
    Provenance,
    RecallContext,
    Sensitivity,
)

pytestmark = pytest.mark.asyncio
TRACKED = CaptureContext(sensitivity=Sensitivity.ORDINARY, inherited=())


def _env(seq: int, payload: object, turn: str | None = "r1") -> Envelope:
    return Envelope(
        session_id="s",
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_700_000_000_000 + seq,
        turn_id=turn,
        trace_id=None,
        payload=payload,
    )  # type: ignore[arg-type]


def _msg(role: str, text: str) -> MessageEnd:
    return MessageEnd(role=role, content=[{"type": "text", "text": text}])


async def _notebooks(notebooks: NotebookClient) -> tuple[NotebookMemory, str, str]:
    store = notebooks
    a = await store.ensure(tenant_id="t", owner="alice", agent_pin="p", name="work")
    b = await store.ensure(tenant_id="t", owner="alice", agent_pin="p", name="home")
    return NotebookMemory(store), a.id, b.id


async def test_recall_fits_the_budget_with_one_heading_per_notebook(notebooks: NotebookClient) -> None:
    memory, work, home = await _notebooks(notebooks)
    await memory.capture(work, ["Owns billing", "Ships on Fridays"], context=TRACKED)
    await memory.capture(home, ["Prefers tea"], context=TRACKED)
    labels = {work: "work", home: "home"}
    recalled = await recall_across(memory, [work, home, work], RecallContext(), labels)
    assert {r.notebook_id for r in recalled} == {work, home}
    assert len(recalled) == 3, "duplicated notebooks are recalled once"
    # A tight budget keeps whole facts only, newest first, headings counted.
    tight = await recall_across(memory, [work], RecallContext(max_chars=45), labels)
    assert [r.text.split(") ", 1)[1] for r in tight] == ["Ships on Fridays"]
    assert await recall_across(memory, [work], RecallContext(max_chars=0)) == []


def test_the_first_recall_shows_everything_later_ones_only_whats_new() -> None:
    a = Recalled("nb", "- (2024-01-01) Prefers tea", "1")
    b = Recalled("nb", "- (2024-01-01) Owns billing", "2")
    labels = {"nb": "work"}
    first = recall_delta([a, b], None, {"nb": {}}, labels)
    assert "### work" in first.text and "Prefers tea" in first.text
    held = {"nb": {"1": shown_of(a).hash, "2": shown_of(b).hash}}
    again = recall_delta([a, b], first.shown, held, labels)
    assert again.text == "", "nothing new: inject nothing"
    c = Recalled("nb", "- (2024-01-02) Ships on Fridays", "3")
    more = recall_delta([a, c], again.shown, {"nb": {**held["nb"], "3": shown_of(c).hash}}, labels)
    assert more.text.startswith("New or updated memory facts") and "Fridays" in more.text
    assert "Prefers tea" not in more.text
    assert {s.id for s in more.shown} == {"1", "2", "3"}, "unchanged b is still held"


def test_removed_and_withdrawn_facts_are_called_out() -> None:
    a = Recalled("nb", "- (2024-01-01) Prefers tea", "1")
    other = Recalled("old", "- (2024-01-01) Secret plan", "9")
    first = recall_delta([a, other], None, {}, {})
    # Fact 1 was consolidated away; the "old" notebook is no longer readable at all.
    out = recall_delta([], first.shown, {"nb": {}}, {"nb": "work"})
    assert "work: 1 earlier fact was consolidated or removed" in out.text
    assert "no longer included" in out.text


def test_shown_records_round_trip_with_provenance() -> None:
    shown = Shown("nb", "1", "abc", Provenance(Sensitivity.SENSITIVE, ("scope:x",), True))
    assert Shown.from_dict(shown.to_dict()) == shown


def test_the_checkpoint_advances_only_when_the_audience_changes() -> None:
    first = boundary.next_context(None, "aud-1", 10)
    assert first == MemoryContext("aud-1", -1), "absent checkpoint is a baseline"
    assert boundary.next_context(first, "aud-1", 20) == first
    moved = boundary.next_context(first, "aud-2", 20)
    assert moved.through_seq == 20
    events = [_env(5, _msg("user", "old")), _env(20, moved), _env(21, _msg("user", "new"))]
    assert [e.seq for e in boundary.bounded(events)] == [21]
    private = Audience.private("alice")
    shared = Audience("alice", "scope:x", ("alice", "bob"))
    assert boundary.fingerprint(private) != boundary.fingerprint(shared)
    assert boundary.fingerprint(shared) == boundary.fingerprint(
        Audience("alice", "scope:x", ("bob", "alice", "bob"))
    )


def test_history_search_is_newest_first_and_opens_entries() -> None:
    events = [
        _env(1, _msg("user", "deploy the billing service")),
        _env(2, ToolStart(tool_call_id="c", tool_name="write", args={"file_path": "billing.md"})),
        _env(3, _msg("assistant", "Billing deployed. " + "x" * 600 + " billing again")),
    ]
    hits = history.search(events, "BILLING")
    assert [h.split("#", 1)[0] for h in hits] == ["assistant", "tool_call", "user"]
    assert len(hits[0]) < 600 and hits[0].endswith("…")
    assert history.open_entry(events, 1).endswith("deploy the billing service")
    assert history.open_entry(events, 99) is None
    assert history.search(events, "  ") == []


async def test_extraction_parses_facts_and_fails_safe() -> None:
    assert parse_facts("NONE") == [] and parse_facts("  none ") == []
    assert parse_facts("SENSITIVITY: ordinary\n- Prefers tea\n* Owns billing\nnot a bullet") == [
        "Prefers tea",
        "Owns billing",
    ]

    async def model(system: str, content: str) -> str:
        assert "User said:\nhi" in content and "Assistant replied:\nhello" in content
        return "SENSITIVITY: sensitive\n- Has a doctor's appointment"

    async def broken(system: str, content: str) -> str:
        raise ConnectionError

    got = await extract_facts(model, [Exchange("hi", "hello")])
    assert (got.facts, got.sensitivity) == (["Has a doctor's appointment"], Sensitivity.SENSITIVE)
    failed = await extract_facts(broken, [Exchange("hi", "hello")])
    assert (failed.facts, failed.sensitivity) == ([], Sensitivity.UNKNOWN)


def test_prompt_section_and_query() -> None:
    assert memory_section("", shared=False, strategy=Strategy.PER_TURN) == ""
    section = memory_section("### work\n- fact", shared=True, strategy=Strategy.AGENT_ONLY)
    assert section.startswith("## What you remember\nYou're in a shared conversation")
    assert "sole curator" in section and section.endswith("### work\n- fact")
    events = [_env(i, _msg("user" if i % 2 else "assistant", f"m{i}")) for i in range(1, 7)]
    query = recall_query("now", events)
    assert query.splitlines()[0] == "user: m3" and query.endswith("Current message: now")


def test_dependencies_include_recalled_provenance_and_untracked_tools() -> None:
    sensitive = Provenance(Sensitivity.SENSITIVE, ("scope:x",), False)
    injected = MemoryInjected("r1", "", [Shown("nb", "1", "h", sensitive).to_dict()])
    assert dependencies([_env(1, injected)]) == (sensitive,)
    with_tool = [_env(1, injected), _env(2, ToolStart(tool_call_id="c", tool_name="ls", args={}))]
    assert set(dependencies(with_tool)) == {sensitive, UNTRACKED}
    memory_tool = [_env(2, ToolStart(tool_call_id="c", tool_name="memory", args={}))]
    assert dependencies(memory_tool) == (), "memory tools are tracked, not untracked context"


async def test_memory_tools_follow_qm_semantics(notebooks: NotebookClient) -> None:
    memory, work, home = await _notebooks(notebooks)
    await memory.capture(home, ["Prefers tea"], context=TRACKED)
    binding = MemoryBinding(
        owner="alice",
        agent_pin="p",
        write=work,
        read=(work, home),
        labels={work: "work", home: "home"},
    )
    tools = TurnMemory(memory, binding, Audience.private("alice"), "s1", dependencies=())

    added = await tools.call("memory", {"action": "add", "facts": [{"text": "Owns billing"}]})
    assert added.text == "Remembered 1 fact."
    again = await tools.call("memory", {"action": "add", "facts": [{"text": "owns BILLING"}]})
    assert again.text == "Already remembered — nothing new to save."
    found = await tools.call("memory", {"action": "search", "query": "tea"})
    assert found.text.startswith(f"[{home}] #"), "multi-notebook hits are tagged"
    own = await tools.call("memory", {"action": "read"})
    assert "Owns billing" in own.text and "tea" not in own.text, "read shows own notebook"
    other = await tools.call("memory", {"action": "read", "notebook": home})
    assert other.text.startswith("### home\n#")
    refused = await tools.call(
        "memory", {"action": "add", "notebook": home, "facts": [{"text": "x"}]}
    )
    assert refused.is_error and "writes stay in this conversation" in refused.text
    bad = await tools.call("memory", {"action": "delete", "ids": ["#999"]})
    assert bad.is_error and "#999" in bad.text
    fact_id = (await memory.facts(work))[0].id
    edited = await tools.call(
        "memory", {"action": "edit", "facts": [{"id": f"#{fact_id}", "text": "Owns payroll"}]}
    )
    assert edited.text == "Updated 1 fact."
    assert (
        await tools.call("memory", {"action": "delete", "ids": [fact_id]})
    ).text == "Forgot 1 fact."
    assert (await tools.call("memory", {"action": "search"})).is_error
    no_memory = TurnMemory(None, None, None, "s1")
    assert (await no_memory.call("memory", {"action": "read"})).text.startswith("[memory isn't")
    assert [s["function"]["name"] for s in no_memory.schemas()] == ["history"]
    held = held_hashes(await memory.facts(home))
    assert list(held) == [(await memory.facts(home))[0].id]
