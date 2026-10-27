"""Reflect: read how an agent's runs went wrong and propose a better prompt.

The evidence is the agent's issues (``supervisor.traces.issues``): failing checks, judges'
fails with their reasons, and runs people marked wrong with their notes, each with the
latest runs it hit (what was asked, what the run did). A model reads the agent's prompt and
that evidence, and proposes edits to the prompt's sections, with why.

A proposal is only a proposal. Nothing here changes the agent: the product turns it into a
candidate version, replays saved runs on it, refuses it if it does worse, and asks a person.
The reflection sees failures from real runs only; it never sees the datasets a candidate is
measured on, nor can it change a judge or a check, so it cannot grade its own work.
The model is asked through a session of the agent's own (its key and budget).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from supervisor.seams.core.types import ChatMessage

__all__ = [
    "MAX_EVIDENCE",
    "REFLECT_MODEL_ENV",
    "REFLECT_SYSTEM",
    "Proposal",
    "ProposalError",
    "evidence_text",
    "parse_proposal",
    "reflect_messages",
]

MAX_EVIDENCE = 8
"""Issues read into one reflection, the ones hitting the most runs first."""
EXAMPLES_PER_ISSUE = 3
REFLECT_MODEL_ENV = "TRACE_REFLECT_MODEL"
"""The model that reflects, when set; otherwise the agent's own model."""
_CLIP = 600

REFLECT_SYSTEM = (
    "You improve the system prompt of an AI agent from evidence of how its runs went wrong. "
    "Read the prompt's sections and the issues, each with runs it hit. Find what in the prompt "
    "causes or fails to prevent each issue, and change as little as needed to fix it: edit a "
    "section's text, or add a section. Keep every section id; never delete a section. Do not "
    "fix one task at the expense of others: write general instructions, never answers to the "
    "example tasks. If the evidence does not point to a prompt change, change nothing and say "
    'why. Answer with JSON only: {"rationale": "what you changed and which issue each change '
    'targets, in a few sentences", "persona": "the persona line, changed or not", "sections": '
    '[{"id": "...", "text": "..."}]} listing every section, in order.'
)


@dataclass(frozen=True)
class Section:
    id: str
    text: str


@dataclass(frozen=True)
class Proposal:
    persona: str
    sections: list[Section]
    rationale: str
    changed: list[str] = field(default_factory=list)
    """Ids of the sections whose text changed or that are new (``persona`` for the persona)."""

    def out(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "sections": [{"id": s.id, "text": s.text} for s in self.sections],
            "rationale": self.rationale,
            "changed": self.changed,
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


def reflect_messages(
    persona: str, sections: list[dict[str, str]], issues: list[dict[str, Any]], notes: str | None = None
) -> list[ChatMessage]:
    prompt = json.dumps({"persona": persona, "sections": sections}, ensure_ascii=False, indent=1)
    ask = f"The agent's prompt:\n{prompt}\n\nHow its runs went wrong:\n{evidence_text(issues)}\n"
    if notes and notes.strip():
        ask += f"\nWhat the person who owns the agent wants addressed:\n{_clip(notes, 1000)}\n"
    return [
        ChatMessage(role="system", content=REFLECT_SYSTEM),
        ChatMessage(role="user", content=ask + "\nYour proposal as JSON:"),
    ]


_JSON = re.compile(r"\{.*\}", re.DOTALL)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProposalError(ValueError):
    """The model's reply is not a usable proposal."""


def parse_proposal(reply: str, persona: str, sections: list[dict[str, str]]) -> Proposal:
    """The proposal in ``reply``; raises ProposalError if it is not one.

    Sections the reply leaves out are kept as they were (a proposal never deletes one), so
    a careless reply can only fail to change something, never lose part of the prompt.
    """
    found = _JSON.search(reply or "")
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise ProposalError("the model's reply was not a proposal")
    current = {s["id"]: s["text"] for s in sections}
    proposed: dict[str, str] = {}
    for s in data["sections"]:
        if not isinstance(s, dict):
            continue
        sid, text = str(s.get("id") or "").strip(), str(s.get("text") or "").strip()
        if not text or not _ID.match(sid) or sid in proposed:
            continue
        proposed[sid] = text[:20_000]
    ordered = [Section(sid, proposed.get(sid, text)) for sid, text in current.items()]
    ordered += [Section(sid, text) for sid, text in proposed.items() if sid not in current]
    changed = [s.id for s in ordered if current.get(s.id) != s.text]
    new_persona = str(data.get("persona") or "").strip()[:2000] or persona
    if new_persona != persona:
        changed.insert(0, "persona")
    rationale = str(data.get("rationale") or "").strip()[:4000] or "no reason given"
    return Proposal(persona=new_persona, sections=ordered, rationale=rationale, changed=changed)
