"""Path normalisation for tool arguments (port of `harness/tools/path-utils.ts`).

Every rule here exists because a path that looks right on screen does not
match the bytes on disk, and the tool then reports "no such file" for a file
that is visibly there — one of the most confusing failures an agent can hit,
because the model's next move is usually to re-read the directory and
conclude the file was deleted.

Two layers:

* **Normalisation**, applied to every path — Unicode spaces and pi's
  leading-`@` file-reference syntax.
* **Variant probing**, for reads only — try several plausible encodings of
  the same visible name and use whichever exists on disk.

Probing is deliberately restricted to reads. Writing to a guessed variant
could create a near-duplicate file that shadows the real one; reading the
wrong file merely returns the wrong text, and only when that exact path
exists.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_UNICODE_SPACES = re.compile("[  -   　]")
NARROW_NO_BREAK_SPACE = " "
RIGHT_SINGLE_QUOTE = "’"


def normalize_tool_path(path: str) -> str:
    """Fold exotic spaces to ASCII and strip a leading `@`.

    Both come from the same place: text that passed through a human. Copying
    a filename out of a terminal, a chat client or macOS Finder routinely
    substitutes a no-break or narrow space, and pi's prompt syntax for
    attaching a file (`@src/main.py`) leaves the `@` in the argument.

    Only a *leading* `@` is syntax — `mail@host.txt` is a real filename.
    """
    normalized = _UNICODE_SPACES.sub(" ", path)
    return normalized[1:] if normalized.startswith("@") else normalized


def _variants(name: str) -> list[str]:
    """Plausible on-disk spellings of the same visible filename.

    Ordered most- to least-likely; the caller takes the first that exists.

    * **AM/PM narrow space** — macOS screenshots are named
      `Screenshot ... 10.30.45 AM.png` with U+202F, which no keyboard types.
    * **NFD** — HFS+/APFS store decomposed Unicode, while clipboards and most
      editors produce composed (NFC). `café` is two different byte sequences
      that render identically.
    * **Curly apostrophe** — macOS "smart quotes" and many editors turn `'`
      into U+2019 when a filename is typed rather than pasted.
    """
    candidates = [
        name,
        re.sub(r" (AM|PM)\.", rf"{NARROW_NO_BREAK_SPACE}\1.", name, flags=re.IGNORECASE),
        unicodedata.normalize("NFD", name),
        unicodedata.normalize("NFC", name),
        name.replace("'", RIGHT_SINGLE_QUOTE),
        unicodedata.normalize("NFD", name).replace("'", RIGHT_SINGLE_QUOTE),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def resolve_read_path(path: str | Path) -> Path:
    """The path to actually open, after normalisation and variant probing.

    Returns the requested path unchanged when no variant exists, so a genuine
    missing file produces a normal "not found" rather than a confusing
    substitution. Probing never falls back to a merely *similar* name —
    silently reading a different file is worse than failing.
    """
    original = Path(normalize_tool_path(str(path)))
    parent, name = original.parent, original.name

    for candidate in _variants(name):
        attempt = parent / candidate
        if attempt.exists():
            return attempt
    return original
