"""Pure text classification shared by consolidation paths (port of `text_utils.rs`).

Kept separate for the same reason Grok did: `dream` would otherwise reach
into the flush path for these helpers, creating a module dependency cycle.
"""

from __future__ import annotations


def has_markdown_headers(text: str) -> bool:
    """Did the model produce structured output?

    Consolidation asks for a markdown document with `##` topic headers. A
    reply with no header at all is usually prose commentary ("Here's what I
    found...") rather than the document, and writing that into MEMORY.md
    would corrupt the store with chat.
    """
    return "## " in text or "# " in text


def is_no_reply(text: str) -> bool:
    """Does the reply match the NO_REPLY convention?

    Normalises away separators and case before comparing, so `NO_REPLY`,
    `no reply`, `No-Reply` and `noreply` are all recognised — models are
    inconsistent about punctuation in sentinel tokens, and treating a
    near-miss as real content would write the literal string "NO_REPLY" into
    memory as though it were knowledge.

    Deliberately exact after normalisation: "no reply needed" is a sentence,
    not the sentinel.
    """
    normalized = "".join(c for c in text.lower() if c.isalnum())
    return normalized == "noreply"
