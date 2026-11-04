"""Session notes: the agent's lessons for the rest of its session (``note_for_session``),
read back from its journal; and, across sessions, as unverified evidence for the loop."""

from __future__ import annotations

import pytest

from supervisor.agent.events import ToolEnd
from supervisor.agent.runtimes.githi.prompt import system_prompt
from supervisor.journal.reader import JournalReader
from supervisor.session.notes import MAX_NOTES, composition_notes, note_of, session_notes
from supervisor.tools.builtin.note_for_session import MAX_NOTE, NoteArgs, note_for_session
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.traces.reflect import shared_notes_text
from tests.support.trace_api import ALICE, Api


def _noted(text: str, *, ok: bool = True, tool: str = "note_for_session") -> ToolEnd:
    return ToolEnd("c", tool, {"ok": ok, "payload": {"note": text} if ok else None, "error": None, "code": None},
                   is_error=not ok)


async def test_a_note_is_one_line_of_at_most_so_many_characters() -> None:
    out = await note_for_session(NoteArgs(content="  Run tests\n  with   make test. "), ToolContext())
    assert out == {"note": "Run tests with make test."}, "one line"
    with pytest.raises(InvalidArguments):
        await note_for_session(NoteArgs(content="   "), ToolContext())
    with pytest.raises(InvalidArguments):
        await note_for_session(NoteArgs(content="x" * (MAX_NOTE + 1)), ToolContext())


def test_only_a_successful_note_for_session_call_is_a_note() -> None:
    assert note_of(_noted("Use make.")) == "Use make."
    assert note_of(_noted("Use make.", ok=False)) is None
    assert note_of(_noted("Use make.", tool="remember")) is None
    assert note_of(object()) is None


def test_the_notes_are_in_the_prompt_after_the_project_and_before_the_cwd() -> None:
    prompt = system_prompt("You fix code.", [], project=[("AGENTS.md", "Be careful.")], notes=["Use make."])
    assert prompt.index("<project_context>") < prompt.index("<session_notes>") < prompt.index("<cwd>")
    assert "<session_notes>" not in system_prompt("You fix code.", [])


async def test_a_sessions_notes_are_its_own_once_each_the_latest(api: Api) -> None:
    sid, other = await api.session(), await api.session()
    await api.journal(sid, "r1", *((n, _noted(f"note {n}")) for n in range(MAX_NOTES + 2)))
    await api.journal(sid, "r2", (100, _noted("note 5")), (101, _noted("bad", ok=False)))
    await api.journal(other, "r1", (1, _noted("theirs")))
    notes = await session_notes(JournalReader(api.factory), sid)
    assert len(notes) == MAX_NOTES and notes[0] == "note 2" and notes[-1] == f"note {MAX_NOTES + 1}"
    assert "bad" not in notes and "theirs" not in notes and notes.count("note 5") == 1


async def test_notes_from_many_sessions_reach_the_reflection_most_shared_first_never_from_excluded(api: Api) -> None:
    a, b, replay = await api.session(), await api.session(), await api.session()
    for sid in (a, b, replay):
        await api.run(sid, "r1", "fix it", start=1_000)
    for sid in (a, b, replay):
        await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    await api.journal(a, "r1", (2_000, _noted("Use make test.")), (2_001, _noted("Only here.")))
    await api.journal(b, "r1", (2_000, _noted("Use make test.")))
    await api.journal(replay, "r1", (2_000, _noted("From a replay.")))
    found = await composition_notes(api.factory, JournalReader(api.factory), tenant_id="t1", principal_id="alice",
                                    composition_id="githi-v1", exclude_sessions=[replay])
    assert found == [{"note": "Use make test.", "sessions": 2}, {"note": "Only here.", "sessions": 1}]
    assert shared_notes_text(found).startswith("- Use make test. (noted in 2 sessions)")
    assert shared_notes_text([]) == "None."
