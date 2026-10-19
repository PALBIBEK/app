"""Automatic capture: extract durable facts from a burst of exchanges (QM per-turn strategy).

The prompt is QM's, verbatim. The model returns bullet facts (or NONE) and a
sensitivity label for the whole list; a failure or malformed answer extracts
nothing and labels "unknown", so capture never invents provenance.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from supervisor.memory.classification import SENSITIVITY_PROMPT, OneShot
from supervisor.memory.render import bullets
from supervisor.memory.types import Sensitivity

logger = logging.getLogger(__name__)

_EXTRACTION_LINES: Final = (
    "You extract durable facts worth remembering about the user across FUTURE conversations.",
    (
        "Given one or more consecutive exchanges (user message + assistant reply), output a "
        "markdown bullet list"
    ),
    "(`- fact`), one concise standalone fact per line, written in the third person",
    "(e.g. `- Prefers terse replies`, `- Owns the billing service`, `- Working on the Q3 launch`).",
    "Include preferences, identifiers, ongoing projects, and how they like to work.",
    ("PROVENANCE: a preference, intent, or instruction is a valid fact ONLY when the user's own"),
    "message in these exchanges states it. Never derive one from the assistant's reply — an",
    ('assistant saying "per X\'s preference" or describing its own strategy ("queued silently to'),
    (
        'avoid spam") is NOT evidence that anyone holds that preference. Likewise EXCLUDE '
        "second-hand"
    ),
    "claims about a person who did not speak in these exchanges.",
    "EXCLUDE secrets/credentials, one-off trivia, and anything already obvious.",
    ("EXCLUDE system mechanics you can look up when needed: API endpoints/headers, credential or"),
    ("broker plumbing, state-file paths, tool invocation details, schemas. For a standing system"),
    ("the user relies on (a cron, a watcher, an integration), record its EXISTENCE and purpose as"),
    ('one fact — not its internals. A user-stated convention ("always via the broker, never raw'),
    'tokens") is a preference and belongs in memory; how the broker works does not.',
    (
        "When the user's own message states an instruction, rule, or directive to the assistant "
        "about"
    ),
    "how future work should be done, record it VERBATIM as a quoted fact",
    (
        '(e.g. `- Directive (user\'s words): "always run the linter before pushing"`), not a '
        "paraphrase."
    ),
    "If nothing is worth remembering, output exactly: NONE",
    SENSITIVITY_PROMPT,
    "Prepend exactly SENSITIVITY: <label> on its own first line, classifying the entire extracted list.",
)
MEMORY_EXTRACTION_PROMPT: Final = "\n".join(_EXTRACTION_LINES)

_SENSITIVITY_LINE = re.compile(r"^SENSITIVITY: (ordinary|unknown|sensitive|restricted)\n")


@dataclass(frozen=True, slots=True)
class Exchange:
    input: str
    reply: str


@dataclass(frozen=True, slots=True)
class Extracted:
    facts: list[str]
    sensitivity: Sensitivity


def parse_facts(out: str) -> list[str]:
    trimmed = out.strip()
    if not trimmed or trimmed.lower() == "none":
        return []
    return [b for b in bullets(trimmed) if b]


def transcript(turns: Sequence[Exchange]) -> str:
    return "\n\n---\n\n".join(
        f"User said:\n{t.input}\n\nAssistant replied:\n{t.reply}" for t in turns
    )


async def extract_facts(one_shot: OneShot, turns: Sequence[Exchange]) -> Extracted:
    try:
        out = await one_shot(MEMORY_EXTRACTION_PROMPT, transcript(turns))
    except Exception:  # a failed extraction remembers nothing, never crashes
        logger.warning("memory extraction failed", exc_info=True)
        return Extracted([], Sensitivity.UNKNOWN)
    match = _SENSITIVITY_LINE.match(out or "")
    return Extracted(parse_facts(out or ""), Sensitivity.parse(match.group(1) if match else None))
