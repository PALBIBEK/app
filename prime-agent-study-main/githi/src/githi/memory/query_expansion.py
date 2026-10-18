"""Stop-word query expansion for FTS-only mode (port of `query_expansion.rs`).

When someone asks *"that thing we discussed about the API"*, FTS5 weights
every word equally — articles, pronouns and vague references dilute precision
until the real term (`API`) is one signal among eight. Stripping stop words
restores it.

    query → lowercase → split on non-alphanumeric → drop stop words → dedup

**The empty return is a contract, not a failure.** When every word is a stop
word ("what is that?"), this returns `[]` to tell the caller there is nothing
worth sending to FTS5, and it should fall back to the vector path — where a
conversational query is exactly what embeddings handle well. Sending the
stripped-to-nothing query anyway would return the whole corpus.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z0-9_]+")

STOP_WORDS = frozenset("""
a an the this that these those there here
i me my we us our you your he she it its they them their
is am are was were be been being do does did done have has had having
can could shall should will would may might must
of in on at to for with by from about into over after before between
and or but not no nor so than then if else when while because as
what which who whom whose where why how
just very really quite also too much many more most some any all
""".split())
"""Function words only: articles, pronouns, prepositions, conjunctions,
auxiliaries, question words.

Deliberately conservative, and the boundary is load-bearing. An earlier
draft also stripped content-ish words (`thing`, `stuff`, `discussed`,
`mentioned`, `said`) on the theory that they are vague. They are not stop
words: "discussed" is a perfectly good query against session logs, and
removing it makes those chunks unreachable with no way for the user to tell
why. Over-stripping is unrecoverable at query time; a surviving common word
merely adds noise the ranker absorbs."""


def expand_query(query: str) -> list[str]:
    """Keywords, deduped, in first-appearance order.

    Order is preserved rather than sorted so the caller can weight earlier
    terms if it wants; dedup keeps a repeated word from double-counting in
    BM25.
    """
    seen: set[str] = set()
    keywords: list[str] = []
    for token in _TOKEN.findall(query.lower()):
        if token in STOP_WORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords
