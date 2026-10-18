"""Skills, disclosed progressively.

A skill is a markdown playbook with YAML-ish frontmatter. The system prompt
carries only names, one-line descriptions and file locations; the model
opens the file with the ordinary `read` tool, exactly as pi does -- no
dedicated skill tool, so skills are simply files.

That split is the whole design. Inlining bodies would make a fifty-skill
library cost fifty bodies of context on *every turn of every run*, including
the runs where none of them are relevant -- so adding a skill would make the
agent globally more expensive, and the rational move would be to keep the
library small. Progressive disclosure inverts that: a skill costs one line
until the moment it is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


class SkillDiagnosticCode(str, Enum):
    """Typed so a caller can treat these differently -- warn loudly on a file
    that could not be read, quietly on one that is merely incomplete."""

    READ_FAILED = "read_failed"
    NO_FRONTMATTER = "no_frontmatter"
    MISSING_NAME = "missing_name"
    MISSING_DESCRIPTION = "missing_description"


@dataclass
class SkillDiagnostic:
    code: SkillDiagnosticCode
    path: str
    message: str


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: str = ""
    """Where the SKILL.md lives.

    Pi advertises this location in the prompt and lets the model open it with
    the ordinary `read` tool rather than shipping a dedicated skill tool --
    one less tool in every request, and skills become just files, so the same
    `grep`/`read` the agent already knows work on them."""


@dataclass
class SkillLibrary:
    skills: list[Skill]
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)
    """Why each skill that did not load, did not load.

    Loading stays lenient -- one broken file must not block the rest, or the
    fix would be to delete a file to get the others back. But silence is the
    wrong report: the user wrote a skill, it is not there, and nothing
    distinguishes a typo from a path mistake from a file githi never saw."""

    @classmethod
    def discover(cls, roots: list[Path]) -> SkillLibrary:
        """Walk each root for `*/SKILL.md`.

        Earlier roots win on a name collision: callers pass bundled skills
        first, so a stray file in a user directory cannot silently replace a
        shipped playbook with one the operator never reviewed.
        """
        found: dict[str, Skill] = {}
        problems: list[SkillDiagnostic] = []
        for root in roots:
            if not root.is_dir():
                continue                      # an absent user skills dir is normal
            for path in sorted(root.glob("*/SKILL.md")):
                skill, diagnostic = _parse(path)
                if diagnostic:
                    problems.append(diagnostic)
                if skill and skill.name not in found:
                    found[skill.name] = skill
        return cls(list(found.values()), problems)

    def get(self, name: str) -> Skill | None:
        return next((s for s in self.skills if s.name == name), None)

    def diagnostics_report(self) -> str:
        """A summary for the user, empty when nothing is wrong.

        Empty rather than "0 problems" so a caller can print it
        unconditionally and stay silent in the normal case.
        """
        if not self.diagnostics:
            return ""
        lines = [f"{len(self.diagnostics)} skill(s) could not be loaded:"]
        lines += [f"  {d.path}: {d.message}" for d in self.diagnostics]
        return "\n".join(lines)

    def listing(self) -> str:
        """Pi's `formatSkillsForSystemPrompt` format.

        Names, descriptions and file locations only -- never bodies. That is
        the progressive-disclosure property: fifty skills cost fifty lines of
        prompt on every turn, not fifty playbooks.
        """
        if not self.skills:
            return ""
        lines = [
            "The following skills provide specialized instructions for specific tasks.",
            "Read the full skill file when the task matches its description.",
            "When a skill file references a relative path, resolve it against the "
            "skill directory (the parent of SKILL.md) and use that path in tool "
            "commands.",
            "",
            "<available_skills>",
        ]
        for s in self.skills:
            lines += [
                "  <skill>",
                f"    <name>{_escape(s.name)}</name>",
                f"    <description>{_escape(s.description)}</description>",
                f"    <location>{_escape(s.path)}</location>",
                "  </skill>",
            ]
        lines.append("</available_skills>")
        return "\n".join(lines)


def _escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _parse(path: Path) -> tuple[Skill | None, SkillDiagnostic | None]:
    """Return the skill, or a diagnostic explaining why there isn't one."""
    def problem(code, message):
        return None, SkillDiagnostic(code, str(path), message)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return problem(SkillDiagnosticCode.READ_FAILED, f"could not read: {exc}")

    match = _FRONTMATTER.match(text)
    if not match:
        return problem(SkillDiagnosticCode.NO_FRONTMATTER,
                       "no YAML frontmatter block. A skill must begin with "
                       "'---', then name: and description:, then '---'.")

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()

    # Falling back to the directory name is a convenience, but it must not
    # hide a typo'd key -- a skill silently named after its folder with no
    # description costs prompt tokens on every turn and tells the model
    # nothing about when to use it.
    if not meta.get("name"):
        return problem(SkillDiagnosticCode.MISSING_NAME,
                       "no 'name:' in the frontmatter (check for a typo in the key).")
    if not meta.get("description"):
        return problem(SkillDiagnosticCode.MISSING_DESCRIPTION,
                       "no 'description:' in the frontmatter. The description is "
                       "what tells the model when the skill applies.")

    return Skill(name=meta["name"], description=meta["description"],
                 body=match.group(2).strip(), path=str(path)), None
