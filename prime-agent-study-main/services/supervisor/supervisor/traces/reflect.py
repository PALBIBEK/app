"""Reflect: read how an agent's runs went wrong and propose small edits to its prompt.

The evidence is the agent's issues (``supervisor.traces.issues``): failing checks, judges'
fails with their reasons, and runs people marked wrong with their notes, each with the
latest runs it hit (what was asked, what the run did). A model reads the agent's prompt,
that evidence, and what became of the changes proposed before (approved, refused by the
gate and why, rejected by a person), and proposes itemized edits, each with why:

- ``add``: append an instruction to a section, or start a new section;
- ``replace``: change one passage of a section (its exact old text, once) for new text.

Edits, not a rewritten prompt. ACE (Agentic Context Engineering, ``ace-agent/ace``) grows
its playbook only by itemized ``ADD`` operations, because rewriting the whole context
erodes what earlier rounds learnt; Prime Agent's ``/refine`` plans "small evidence-backed"
create/update/delete edits from the trajectory and its prior refinement history
(``pa-core/src/refinement/planner.rs``), and never rewrites the base prompt. Here no edit
deletes a section, and an edit that cannot be applied (an unknown passage, a bad id) is
skipped and reported, never guessed.

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
    "REFLECT_MODEL_ENV",
    "REFLECT_SYSTEM",
    "Proposal",
    "ProposalError",
    "evidence_text",
    "history_text",
    "parse_proposal",
    "reflect_messages",
]

MAX_EVIDENCE = 8
"""Issues read into one reflection, the ones hitting the most runs first."""
EXAMPLES_PER_ISSUE = 3
REFLECT_MODEL_ENV = "TRACE_REFLECT_MODEL"
"""The model that reflects, when set; otherwise the agent's own model."""
_CLIP = 600

MAX_EDITS = 8
"""Edits one proposal may make: a change a person can review, and the gate can attribute."""
MAX_HISTORY = 20
"""Earlier proposals shown, the latest (Prime Agent's ``history_for_prompt`` keeps 20)."""

REFLECT_SYSTEM = (
    "You improve the system prompt of an AI agent from evidence of how its runs went wrong. "
    "Read the prompt's sections, the issues (each with runs it hit), and the changes proposed "
    "before with what became of them. Find what in the prompt causes or fails to prevent each "
    "issue, and propose the fewest, smallest edits that fix it. Do not repeat a change that was "
    "refused or rejected; if an approved change did not fix its issue, change it rather than "
    "adding another. Write general instructions, never answers to the example tasks, and do "
    "not fix one task at the expense of others. Edits: "
    '{"action": "add", "section": "<id>", "text": "..."} appends an instruction to a section '
    "(a new id starts a new section); "
    '{"action": "replace", "section": "<id>", "old": "<exact text in that section>", "new": "..."} '
    "changes one passage. Sections are never deleted. If the evidence does not point to a "
    f"prompt change, propose no edits and say why. At most {MAX_EDITS} edits. Answer with JSON "
    'only: {"rationale": "which issue each edit targets, in a few sentences", "expected_outcome": '
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
    """``add`` or ``replace``."""
    section: str
    text: str
    """What is added, or the new text of the passage."""
    old: str | None = None
    """The passage replaced (``replace``)."""
    reason: str = ""

    def out(self) -> dict[str, Any]:
        return {"action": self.action, "section": self.section, "text": self.text, "old": self.old,
                "reason": self.reason}


@dataclass(frozen=True)
class Proposal:
    persona: str
    sections: list[Section]
    rationale: str
    changed: list[str] = field(default_factory=list)
    """Ids of the sections whose text changed or that are new."""
    edits: list[Edit] = field(default_factory=list)
    """The edits applied, in order."""
    skipped: list[str] = field(default_factory=list)
    """Edits the reply asked for that could not be applied, and why."""
    expected_outcome: str = ""

    def out(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "sections": [{"id": s.id, "text": s.text} for s in self.sections],
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
                lines.append(f"  - {e.get('action')} in {e.get('section')}: {_clip(e.get('text'), 200)}")
        for reason in (past.get("why") or [])[:4]:
            lines.append(f"  what came of it: {_clip(str(reason), 300)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def reflect_messages(
    persona: str,
    sections: list[dict[str, str]],
    issues: list[dict[str, Any]],
    notes: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> list[ChatMessage]:
    prompt = json.dumps({"persona": persona, "sections": sections}, ensure_ascii=False, indent=1)
    ask = (
        f"The agent's prompt:\n{prompt}\n\nHow its runs went wrong:\n{evidence_text(issues)}\n\n"
        f"Changes proposed before, and what became of them:\n{history_text(history or [])}\n"
    )
    if notes and notes.strip():
        ask += f"\nWhat the person who owns the agent wants addressed:\n{_clip(notes, 1000)}\n"
    return [
        ChatMessage(role="system", content=REFLECT_SYSTEM),
        ChatMessage(role="user", content=ask + "\nYour edits as JSON:"),
    ]


_JSON = re.compile(r"\{.*\}", re.DOTALL)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProposalError(ValueError):
    """The model's reply is not a usable proposal."""


def parse_proposal(reply: str, persona: str, sections: list[dict[str, str]]) -> Proposal:
    """The edits in ``reply`` applied to the prompt; raises ProposalError if it is not a proposal.

    Edits apply in order, each to the text the earlier ones left. One that cannot apply (an
    unknown action, a bad section id, an ``old`` passage not found exactly once, nothing to
    add) is skipped with why; an edit never deletes a section. The persona is not edited.
    """
    found = _JSON.search(reply or "")
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
        raise ProposalError("the model's reply was not a proposal")
    current = {s["id"]: s["text"] for s in sections}
    texts = dict(current)
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
        sid = str(raw.get("section") or "").strip()
        reason = str(raw.get("reason") or "").strip()[:500]
        if not _ID.match(sid):
            skipped.append(f"edit {n}: {sid!r} is not a section id")
            continue
        if action == "add":
            text = str(raw.get("text") or "").strip()
            if not text:
                skipped.append(f"edit {n}: nothing to add")
                continue
            texts[sid] = f"{texts[sid].rstrip()}\n{text}" if texts.get(sid) else text
            applied.append(Edit("add", sid, text[:20_000], None, reason))
        elif action == "replace":
            old, new = str(raw.get("old") or ""), str(raw.get("new") or "").strip()
            body = texts.get(sid)
            if body is None:
                skipped.append(f"edit {n}: there is no section {sid!r} to change")
            elif not old.strip() or body.count(old) != 1:
                skipped.append(f"edit {n}: the passage to replace is not in {sid!r} exactly once")
            elif not new:
                skipped.append(f"edit {n}: no new text for the passage (edits never delete)")
            else:
                texts[sid] = body.replace(old, new, 1)
                applied.append(Edit("replace", sid, new[:20_000], old, reason))
        else:
            skipped.append(f"edit {n}: unknown action {action!r}")
    ordered = [Section(sid, texts[sid][:20_000]) for sid in current]
    ordered += [Section(sid, text[:20_000]) for sid, text in texts.items() if sid not in current]
    changed = [s.id for s in ordered if current.get(s.id) != s.text]
    rationale = str(data.get("rationale") or "").strip()[:4000] or "no reason given"
    expected = str(data.get("expected_outcome") or "").strip()[:2000]
    return Proposal(persona=persona, sections=ordered, rationale=rationale, changed=changed, edits=applied,
                    skipped=skipped, expected_outcome=expected)
