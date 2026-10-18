"""Session memory for githi — a port of xai's `xai-grok-memory` crate.

The central design decision, copied deliberately because everything else
follows from it:

    **Markdown files are the source of truth. SQLite is a disposable index.**

Everything stored is plain `.md` a human can open, edit or delete. The
database exists only to make that corpus searchable and can be thrown away
and rebuilt from the files at any time. That inverts the usual arrangement
(database of record, files as export) and buys three things: a user can fix a
wrong memory in a text editor, a corrupted index is never data loss, and the
whole store is greppable without this package.

## Layout

    ~/.githi/memory/
      ├── MEMORY.md                             # global, cross-project
      └── {slug}-{hash8}/                       # per-workspace
          ├── MEMORY.md                         # curated project knowledge
          └── sessions/
              └── YYYY-MM-DD-{slug}-{run8}.md   # raw run logs

## Deliberately absent: any measure of whether a memory helped

Grok tracks `access_count` — how often a chunk was *retrieved* — and nothing
about whether retrieving it improved an outcome. Its boost is
`1.0 + ln1p(access_count) * 0.05`: a popularity signal, and carefully
incapable of penalising, since the floor is 1.0 and never lower.

That restraint is copied on purpose, against a measured alternative. A
related system computed a real fitness signal (API-call deltas, per-entry
worth) and showed **0% improvement** across runs — partly because the gate
deciding which costs counted was the agent's own self-report. Closed-loop
control on a signal the controlled system generates about itself is worse
than no loop at all.

So this module accumulates and retrieves operational knowledge. It does not
claim, and cannot measure, self-improvement. Answering "does memory help?"
belongs in an external A/B benchmark, where a control actually exists.

## Deviations from the Rust original

* **blake2b, not blake3** — stdlib, no dependency. Same role (content hashing
  for change detection); digests differ, which is irrelevant because the
  index is disposable.
* **FTS5-only until the vector layer lands** — this is Grok's own documented
  degradation path (`text_weight = 1.0` when sqlite-vec is unavailable), not
  a shortcut around it.
"""

from __future__ import annotations

from .chunker import Chunk, chunk_markdown
from .index import IndexedChunk, IndexResult, MemoryIndex
from .storage import MemoryScope, MemoryStorage, is_scaffold_template, workspace_key

__all__ = [
    "Chunk",
    "IndexResult",
    "IndexedChunk",
    "MemoryIndex",
    "MemoryScope",
    "MemoryStorage",
    "chunk_markdown",
    "is_scaffold_template",
    "workspace_key",
]
