"""The system prompt githi sends, in pi's structure.

pi's principle: the prompt describes the tools and gets out of the way; guidance that
narrows how the model may work costs more than the tokens it saves. So the prompt is:

* the **preamble**: the pinned prompt pack (who the agent is, the owner's instructions);
* ``<tools>``: one line per tool the run offers (the full schema reaches the model in the
  tool definitions, so a line only has to jog recognition);
* ``<rules>``: the few rules the offered tools bring (use ``edit`` for precise changes,
  ``write`` only for new files …) and pi's two standing ones;
* ``<cwd>``: where the agent works.

It is built only from the pinned prompt and the offered tools, so it is identical from
step to step and run to run until either changes: the prefix stays cacheable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

__all__ = [
    "BASE_RULES",
    "PROJECT_ROOT",
    "ROOT_RULE",
    "TOOL_ORDER",
    "WORKSPACE",
    "in_pi_order",
    "system_prompt",
    "tool_line",
]

WORKSPACE: Final = "/workspace"
"""Where the file tools root their paths (``/workspace/x`` and ``x`` are the same file)."""

PROJECT_ROOT: Final = "."
"""The ``<cwd>`` githi shows: the project root, as a relative path. A sandbox's commands
run at its root, which is ``/workspace`` in a container but a directory of its own for a
host-process sandbox, so an absolute path in the prompt was wrong for one of them (a live
run spent eight steps looking for ``/workspace``)."""

ROOT_RULE: Final = (
    "Your working directory is the project root: bash runs there, and file paths are "
    "relative to it (e.g. src/app.py)"
)

SNIPPETS: Final[dict[str, str]] = {
    "read": "Read file contents",
    "bash": "Execute bash commands (ls, grep, find, etc.)",
    "edit": "Make precise file edits with exact text replacement, including multiple disjoint edits in one call",
    "write": "Create or overwrite files",
    "grep": "Search file contents for patterns",
    "ls": "List directory contents",
}
"""pi's one-line snippets for the tools githi shares with it; other tools use the first
sentence of their own description."""

RULES: Final[dict[str, tuple[str, ...]]] = {
    "read": ("Use read to examine files instead of cat or sed.",),
    "edit": (
        "Use edit for precise changes (old_text must match exactly)",
        (
            "When changing multiple separate locations in one file, use one edit call with "
            "multiple entries in edits instead of multiple edit calls"
        ),
        (
            "Each edits[].old_text is matched against the original file, not after earlier edits "
            "are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit."
        ),
        (
            "Keep old_text as small as possible while still being unique in the file. Do not pad "
            "with large unchanged regions."
        ),
    ),
    "write": ("Use write only for new files or complete rewrites.",),
}

BASE_RULES: Final = ("Be concise in your responses", "Show file paths clearly when working with files")

TOOL_ORDER: Final = ("read", "bash", "edit", "write", "grep", "find", "ls")
"""The order pi offers its tools in (its defaults first); other tools follow, as given."""


def in_pi_order(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """``tools`` with pi's own tools first, in pi's order; the rest keep their order."""
    rank = {name: n for n, name in enumerate(TOOL_ORDER)}
    return sorted(tools, key=lambda t: rank.get(_name(t), len(rank)))


def _project_context(files: Sequence[tuple[str, str]]) -> str:
    blocks = [
        f'<project_instructions path="{path}">\n{text.strip()}\n</project_instructions>' for path, text in files
    ]
    return "\n\n".join(["Project-specific instructions and guidelines:", *blocks])


def _name(schema: dict[str, Any]) -> str:
    return str((schema.get("function") or {}).get("name") or "")


def _summary(description: str) -> str:
    first = description.strip().split("\n")[0].strip()
    return first.split(". ")[0].rstrip(".")


def tool_line(schema: dict[str, Any]) -> str:
    name = _name(schema)
    snippet = SNIPPETS.get(name) or _summary(str((schema.get("function") or {}).get("description") or ""))
    return f"- {name}: {snippet}" if snippet else f"- {name}"


def _rules(names: Sequence[str]) -> list[str]:
    rules: list[str] = []
    if "bash" in names and not {"grep", "ls", "find"} & set(names):
        rules.append("Use bash for file operations like ls, rg, find")
    for name in names:
        rules += [r for r in RULES.get(name, ()) if r not in rules]
    return [*rules, *BASE_RULES]


def system_prompt(
    pinned: str,
    tools: Sequence[dict[str, Any]],
    *,
    cwd: str = PROJECT_ROOT,
    project: Sequence[tuple[str, str]] = (),
) -> str:
    """The pinned prompt, then the offered tools, their rules, the project's own
    instructions (``AGENTS.md`` and the like: ``project`` is (path, text) pairs), and
    the working directory, in pi's order."""
    tools = in_pi_order(tools)
    names = [n for n in (_name(t) for t in tools) if n]
    sections = [pinned.strip()] if pinned.strip() else []
    if tools:
        listing = "\n".join(tool_line(t) for t in tools)
        sections.append(
            f"<tools>\n{listing}\n\nIn addition to the tools above, you may have access to other "
            "custom tools depending on the project.\n</tools>"
        )
    rules = "\n".join(f"- {r}" for r in [*([ROOT_RULE] if cwd == PROJECT_ROOT and tools else []), *_rules(names)])
    sections.append(f"<rules>\n{rules}\n</rules>")
    if project:
        sections.append(f"<project_context>\n{_project_context(project)}\n</project_context>")
    sections.append(f"<cwd>\n{cwd}\n</cwd>")
    return "\n\n".join(sections)
