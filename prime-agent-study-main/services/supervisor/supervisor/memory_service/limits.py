"""How much the memory service takes in one request and keeps in one notebook.

Without bounds one call could add thousands of facts, or a megabyte as one fact, and
every later recall reads the whole notebook. The limits are generous for what memory
is for (short, self-contained facts) and small enough to keep a turn's recall cheap.
"""

from __future__ import annotations

import re
from typing import Final

MAX_FACTS_PER_CAPTURE: Final = 200
MAX_FACT_CHARS: Final = 4_000
MAX_FACTS_PER_NOTEBOOK: Final = 5_000
MAX_ITEM_CHARS: Final = 8_000
"""A bank item's content (extraction keeps 400; written items get more room)."""
MAX_RECALL_TOKENS: Final = 32_000
MAX_PENDING_EVENTS: Final = 10_000
"""Unflushed events one session may hold in a replica's memory (flushes are checkpoints)."""
BUFFER_IDLE_MS: Final = 10 * 60_000
"""A buffer untouched this long is flushed by the sweep, or dropped if its session is gone."""

_ESCAPED_NUL: Final = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")
"""A NUL inside a JSON string: an odd run of backslashes before ``u0000``. JSON cannot
carry a raw NUL, so this is the only way one arrives (Postgres stores none in text)."""


def has_nul(body: bytes) -> bool:
    """Whether a JSON request body carries a NUL character in any string."""
    return b"u0000" in body and _ESCAPED_NUL.search(body) is not None
