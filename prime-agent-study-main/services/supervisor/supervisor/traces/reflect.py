"""Reflect: read how an agent's runs went wrong and propose edits to what it has learnt.

The evidence is the agent's issues (``supervisor.traces.issues``): failing checks, judges'
fails with their reasons, and runs people marked wrong with their notes, each with the
latest runs it hit (what was asked, what the run did). A few of those runs are also read in
full (``trajectory_record``): what was asked, every step the agent took, and why it was judged
a failure. This is GEPA's reflective dataset (``gepa/core/adapter.py``
``make_reflective_dataset``): per example, ``Inputs``, ``Generated Outputs`` (the trajectory)
and ``Feedback``, on a minibatch of 3 by default (``reflection_minibatch_size``); a summary
says that a run failed, the trajectory shows where. A model reads the agent's prompt,
that evidence, and what became of the changes proposed before (approved, refused by the
gate and why, rejected by a person), and proposes itemized edits, each with why.

The prompt has two parts. The person's sections are the base: the reflection reads them and
never changes them. What the loop learns is a separate list of notes, each with an id, sent
after the sections; the reflection adds, updates or deletes notes:

- ``add``: a new note (its id is ``note-00001``, the next number);
- ``update``: a note's new text (by id);
- ``delete``: a note that no longer helps or that hurt (by id).

This is Prime Agent's ``/refine`` (``crates/pa-core/src/refinement/planner.rs``): "The base
system prompt is immutable and MUST NOT be rewritten"; refinement emits create, update or
delete edits to supplemental prompt notes, each with a stable id, from the trajectory and its
prior refinement history. Note ids follow ACE's playbook bullets (``ace-agent/ace``
``playbook_utils.py``: ``{slug}-{next_id:05d}``, the next global id), and edits are itemized
deltas, never a rewrite. An edit that cannot apply (an unknown id, nothing to add, too many
notes) is skipped and reported, never guessed.

A proposal is only a proposal. Nothing here changes the agent: the product turns it into a
candidate version, replays saved runs on it, refuses it if it does worse, and asks a person.
The reflection sees failures from real runs only; it never sees an experiment's replays or
held-out saved runs, nor can it change a judge or a check, so it cannot grade its own work.
The model is asked through a session of the agent's own (its key and budget).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from supervisor.seams.core.types import ChatMessage

__all__ = [
    "MAX_EDITS",
    "MAX_EVIDENCE",
    "MAX_HISTORY",
    "MAX_NOTES",
    "REFLECT_MODEL_ENV",
    "REFLECT_SYSTEM",
    "TRAJECTORIES",
    "Proposal",
    "ProposalError",
    "evidence_text",
    "history_text",
    "parse_proposal",
    "reflect_messages",
    "trajectories_text",
    "trajectory_record",
]

TRAJECTORIES = 3
"""Failing runs read in full per reflection (GEPA's default ``reflection_minibatch_size``)."""
TRAJECTORY_LIMIT = 6_000
"""Characters of one run read in full (the middle is cut first, as ``run_text`` does)."""
MAX_EVIDENCE = 8
"""Issues read into one reflection, the ones hitting the most runs first."""
EXAMPLES_PER_ISSUE = 3
REFLECT_MODEL_ENV = "TRACE_REFLECT_MODEL"
"""The model that reflects, when set; otherwise the agent's own model."""
_CLIP = 600

MAX_EDITS = 8
"""Edits one proposal may make: a change a person can review, and the gate can attribute."""
MAX_NOTES = 30
"""Notes an agent keeps: past this, a note must be updated or deleted to add one."""
MAX_NOTE = 2000
MAX_HISTORY = 20
"""Earlier proposals shown, the latest (Prime Agent's ``history_for_prompt`` keeps 20)."""

REFLECT_SYSTEM = (
    "You improve an AI agent from evidence of how its runs went wrong. Its prompt is the "
    "person's sections, which you must not change, and a list of notes it has learnt, which you "
    "edit. Read the sections, the notes, the issues (each with runs it hit), and the changes "
    "proposed before with what became of them. Find what the notes should say to fix each issue, "
    "and propose the fewest, smallest edits. Do not repeat a change that was refused or "
    "rejected; if an approved note did not fix its issue, update or delete it rather than adding "
    "another. Write general instructions, never answers to the example tasks, and do not fix one "
    "task at the expense of others. Edits: "
    '{"action": "add", "text": "..."} adds a note; '
    '{"action": "update", "id": "<note id>", "text": "..."} rewrites one note; '
    '{"action": "delete", "id": "<note id>"} removes one. If the evidence does not point to a '
    f"change, propose no edits and say why. At most {MAX_EDITS} edits. Answer with JSON only: "
    '{"rationale": "which issue each edit targets, in a few sentences", "expected_outcome": '
    '"what should improve, and how the saved runs would show it", "edits": [{..., "reason": '
    '"why, from the evidence"}]}'
)


@dataclass(frozen=True)
class Section:
    id: str
    text: str


@dataclass(frozen=True)
class Edit:
    action: str
    """``add``, ``update`` or ``delete``."""
    id: str
    """The note's id (a new one for ``add``)."""
    text: str
    """The note's text after the edit (before it, for ``delete``)."""
    old: str | None = None
    """The note's text before an ``update``."""
    reason: str = ""

    def out(self) -> dict[str, Any]:
        return {"action": self.action, "id": self.id, "text": self.text, "old": self.old, "reason": self.reason}


@dataclass(frozen=True)
class Proposal:
    persona: str
    sections: list[Section]
    """The person's sections, as they were."""
    learned: list[Section]
    """The notes after the edits, in order."""
    rationale: str
    changed: list[str] = field(default_factory=list)
    """Ids of the notes added, updated or deleted."""
    edits: list[Edit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Edits the reply asked for that could not be applied, and why."""
    expected_outcome: str = ""

    def out(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "sections": [{"id": s.id, "text": s.text} for s in self.sections],
            "learned": [{"id": n.id, "text": n.text} for n in self.learned],
            "rationale": self.rationale,
            "changed": self.changed,
            "edits": [e.out() for e in self.edits],
            "skipped": self.skipped,
            "expected_outcome": self.expected_outcome,
        }


def _clip(text: str | None, limit: int = _CLIP) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def evidence_text(issues: list[dict[str, Any]]) -> str:
    """The issues as the model reads them: title, how many runs, then examples."""
    blocks: list[str] = []
    for n, issue in enumerate(issues[:MAX_EVIDENCE], 1):
        lines = [f"Issue {n}: {issue['title']} ({issue['runs']} run{'s' if issue['runs'] != 1 else ''})"]
        for ex in issue.get("examples", [])[:EXAMPLES_PER_ISSUE]:
            lines.append(f"- asked: {_clip(ex.get('input'), 300) or '(not recorded)'}")
            if ex.get("summary"):
                lines.append(f"  the run: {_clip(ex['summary'], 300)}")
            if ex.get("reason"):
                lines.append(f"  why it failed: {_clip(ex['reason'], 300)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def history_text(history: list[dict[str, Any]]) -> str:
    """Earlier proposals, oldest first: what each changed and why, and what became of it."""
    if not history:
        return "No change was proposed before."
    blocks: list[str] = []
    for n, past in enumerate(history[-MAX_HISTORY:], 1):
        lines = [f"Change {n}: {past.get('outcome') or 'unknown'}"]
        if past.get("rationale"):
            lines.append(f"  why it was proposed: {_clip(past['rationale'], 400)}")
        for e in (past.get("edits") or [])[:MAX_EDITS]:
            if isinstance(e, dict):
                lines.append(f"  - {e.get('action')} {e.get('id')}: {_clip(e.get('text'), 200)}")
        for reason in (past.get("why") or [])[:4]:
            lines.append(f"  what came of it: {_clip(str(reason), 300)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def trajectory_record(ask: str | None, run: str, feedback: list[str]) -> dict[str, str]:
    """One failing run as the model reads it: GEPA's reflective-dataset record."""
    return {
        "Inputs": _clip(ask, 1000) or "(not recorded)",
        "Generated Outputs": run,
        "Feedback": "; ".join(_clip(f, 300) for f in feedback) or "judged a failure",
    }


def trajectories_text(records: list[dict[str, str]]) -> str:
    blocks = [
        f"Run {n}:\n[Inputs]\n{r['Inputs']}\n[Generated Outputs]\n{r['Generated Outputs']}\n[Feedback]\n{r['Feedback']}"
        for n, r in enumerate(records[:TRAJECTORIES], 1)
    ]
    return "\n\n".join(blocks) if blocks else "None read."


def reflect_messages(
    persona: str,
    sections: list[dict[str, str]],
    issues: list[dict[str, Any]],
    notes: str | None = None,
    history: list[dict[str, Any]] | None = None,
    learned: list[dict[str, str]] | None = None,
    trajectories: list[dict[str, str]] | None = None,
) -> list[ChatMessage]:
    prompt = json.dumps({"persona": persona, "sections": sections}, ensure_ascii=False, indent=1)
    kept = json.dumps(learned or [], ensure_ascii=False, indent=1)
    ask = (
        f"The person's prompt (read only):\n{prompt}\n\nThe notes it has learnt (yours to edit):\n{kept}\n\n"
        f"How its runs went wrong:\n{evidence_text(issues)}\n\n"
        f"Some of those runs in full, step by step:\n{trajectories_text(trajectories or [])}\n\n"
        f"Changes proposed before, and what became of them:\n{history_text(history or [])}\n"
    )
    if notes and notes.strip():
        ask += f"\nWhat the person who owns the agent wants addressed:\n{_clip(notes, 1000)}\n"
    return [
        ChatMessage(role="system", content=REFLECT_SYSTEM),
        ChatMessage(role="user", content=ask + "\nYour edits as JSON:"),
    ]


_JSON = re.compile(r"\{.*\}", re.DOTALL)
_NOTE_ID = re.compile(r"^note-(\d{5})$")


class ProposalError(ValueError):
    """The model's reply is not a usable proposal."""


def _next_id(notes: dict[str, str]) -> int:
    """ACE's ``get_next_global_id``: one past the highest number any note id carries."""
    numbers = [int(m.group(1)) for nid in notes if (m := _NOTE_ID.match(nid))]
    return max(numbers, default=0) + 1


def parse_proposal(
    reply: str, persona: str, sections: list[dict[str, str]], learned: list[dict[str, str]] | None = None
) -> Proposal:
    """The edits in ``reply`` applied to the notes; raises ProposalError if it is not a proposal.

    Edits apply in order, each to the notes the earlier ones left. One that cannot apply is
    skipped with why. The person's sections and persona are returned as they were.
    """
    found = _JSON.search(reply or "")
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
        raise ProposalError("the model's reply was not a proposal")
    before = {n["id"]: n["text"] for n in learned or []}
    notes = dict(before)
    applied: list[Edit] = []
    skipped: list[str] = []
    for n, raw in enumerate(data["edits"], 1):
        if len(applied) == MAX_EDITS:
            skipped.append(f"edit {n}: more than {MAX_EDITS} edits")
            continue
        if not isinstance(raw, dict):
            skipped.append(f"edit {n}: not an edit")
            continue
        action = str(raw.get("action") or "").strip().lower()
        nid = str(raw.get("id") or "").strip()
        text = " ".join(str(raw.get("text") or "").split())[:MAX_NOTE]
        reason = str(raw.get("reason") or "").strip()[:500]
        if action == "add":
            if not text:
                skipped.append(f"edit {n}: nothing to add")
            elif len(notes) >= MAX_NOTES:
                skipped.append(f"edit {n}: the agent keeps at most {MAX_NOTES} notes; update or delete one first")
            elif text in notes.values():
                skipped.append(f"edit {n}: that note is already kept")
            else:
                nid = f"note-{_next_id({**before, **notes}):05d}"  # a note deleted here keeps its number
                notes[nid] = text
                applied.append(Edit("add", nid, text, None, reason))
        elif action in ("update", "delete"):
            if nid not in notes:
                skipped.append(f"edit {n}: there is no note {nid!r}")
            elif action == "delete":
                applied.append(Edit("delete", nid, notes.pop(nid), None, reason))
            elif not text:
                skipped.append(f"edit {n}: no new text for {nid} (to remove it, delete it)")
            else:
                applied.append(Edit("update", nid, text, notes[nid], reason))
                notes[nid] = text
        else:
            skipped.append(f"edit {n}: unknown action {action!r}")
    changed = list(dict.fromkeys(e.id for e in applied if before.get(e.id) != notes.get(e.id)))
    rationale = str(data.get("rationale") or "").strip()[:4000] or "no reason given"
    expected = str(data.get("expected_outcome") or "").strip()[:2000]
    return Proposal(
        persona=persona,
        sections=[Section(s["id"], s["text"]) for s in sections],
        learned=[Section(nid, text) for nid, text in notes.items()],
        rationale=rationale, changed=changed, edits=applied, skipped=skipped, expected_outcome=expected,
    )
