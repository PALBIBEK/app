"""The system prompt, ported from pi's `core/system-prompt.ts`.

**Why this is a port and not an improvement.** An earlier version of this file
was written from scratch and tried to be helpful: it explained that turns are
expensive, that the sandbox holds no credentials, and that authenticated
network calls would fail -- concluding "work with what is on disk and what the
task gives you."

Every sentence of that was true and the combination was actively harmful. Told
the environment had no credentials, the model inferred the network was closed;
told to work with what was on disk, it stopped considering fetching anything.
On a commit-archaeology task it then failed three times while *recommending in
its own answer* the command that would have solved it in one step
(`git clone` + `git log -S`). Pi, whose prompt says nothing whatsoever about
networks, credentials or cost, simply cloned the repository and answered
correctly on the first attempt.

The lesson is pi's actual design principle: **the prompt describes the tools
and gets out of the way.** Guidance that narrows the solution space costs more
than the tokens it saves, because the failure is invisible -- a model that
never considers an approach does not report that it skipped one.
"""

from __future__ import annotations

from ..tools.base import Tool

HEADER = (
    "You are an expert coding assistant operating inside githi, a coding agent "
    "harness. You help users by reading files, executing commands, editing "
    "code, and writing new files."
)

# Pi's two unconditional guidelines. Deliberately not extended: each addition
# is a constraint on how the model may think, and the bar for adding one is
# evidence that its absence caused a failure -- not a hunch that it might help.
# Tool usage guidance belongs in each tool's description (sent via to_wire),
# not here -- see remember / ask_user_question.
BASE_GUIDELINES = (
    "Be concise in your responses",
    "Show file paths clearly when working with files",
)


def _snippet(tool: Tool) -> str:
    """One line per tool for the prompt's inventory, matching pi's
    `toolSnippets`. The full schema still reaches the model through the tool
    definitions themselves, so this only has to jog recognition."""
    first = tool.description.strip().split("\n")[0].strip()
    return first.split(". ")[0].rstrip(".") if first else tool.name


def build_system_prompt(
    cwd: str = ".",
    tools: list[Tool] | None = None,
    skill_listing: str = "",
    extra: str = "",
    custom: str = "",
) -> str:
    """Assemble the prompt. `custom` replaces the default entirely (pi's
    `customPrompt`), while context and skills are still appended."""
    parts: list[str] = []

    if custom.strip():
        parts.append(custom.strip())
    else:
        parts.append(HEADER)
        if tools:
            listing = "\n".join(f"- {t.name}: {_snippet(t)}" for t in tools)
            parts.append(f"Available tools:\n{listing}\n\nIn addition to the tools "
                         "above, you may have access to other custom tools depending "
                         "on the project.")
        parts.append("Guidelines:\n" + "\n".join(f"- {g}" for g in BASE_GUIDELINES))

    if extra.strip():
        parts.append(extra.strip())
    if skill_listing.strip():
        parts.append(skill_listing.strip())

    parts.append(f"Current working directory: {cwd}")
    return "\n\n".join(parts)
