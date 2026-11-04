"""Ranking a notebook's facts for a turn's recall: relevant to the conversation first.

Recall fits facts into a character budget (``supervisor.memory.cross``), so the order
decides what a turn remembers. Newest-first alone loses an old fact the question needs
("allergic to peanuts") behind newer chatter. Facts are scored by BM25 against the
recall query (the recent exchanges plus the current message, which counts double),
then by recency: a fact sharing no word with the conversation keeps the flat
fallback score, so with no query, or nothing relevant, recall is newest first as before.

Scores fall in [0.2, 1.0), so they rank against other notebooks' candidates on one scale.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Final

from supervisor.memory_service.notebooks.render import candidate
from supervisor.memory_service.notebooks.types import Candidate, Fact

FLOOR: Final = 0.2
"""The score of a fact that matches nothing (and of every fact without a query)."""
_K1: Final = 1.2
_B: Final = 0.75
_HALF: Final = 2.0
"""The raw BM25 score that maps to halfway between FLOOR and 1."""
_CURRENT: Final = "current message:"

_WORD = re.compile(r"\w+")
_ROLE = re.compile(r"^(?:user|assistant):\s*", re.IGNORECASE)
_STOP: Final = frozenset(
    ["a", "about", "above", "after", "again", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves", "let", "lets", "please", "ok", "okay", "yes", "also", "get", "got", "one"]
)


def terms(text: str) -> list[str]:
    """Content words, case-folded, with a plural's s dropped (so "peanuts" meets "peanut")."""
    out = []
    for word in _WORD.findall(unicodedata.normalize("NFKC", text).casefold()):
        if word in _STOP or (len(word) < 2 and not word.isdigit()):
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def query_weights(query: str) -> dict[str, float]:
    """Each query term's weight: the current message counts double the history before it."""
    weights: dict[str, float] = {}
    for line in query.splitlines():
        stripped = line.strip()
        weight = 1.0
        if stripped.casefold().startswith(_CURRENT):
            stripped, weight = stripped[len(_CURRENT):], 2.0
        for term in set(terms(_ROLE.sub("", stripped))):
            weights[term] = max(weights.get(term, 0.0), weight)
    return weights


def ranked_candidates(facts: Sequence[Fact], query: str | None) -> list[Candidate]:
    """Candidates for ``facts``: by relevance to ``query``, then newest first."""
    newest_first = list(reversed(facts))
    weights = query_weights(query) if query else {}
    if not weights or not facts:
        return [candidate(f, FLOOR) for f in newest_first]
    docs = [Counter(terms(f.text)) for f in newest_first]
    scores = _bm25(docs, weights)
    order = sorted(range(len(newest_first)), key=lambda i: -scores[i])  # stable: recency breaks ties
    return [candidate(newest_first[i], _scale(scores[i])) for i in order]


def _bm25(docs: Sequence[Counter[str]], weights: dict[str, float]) -> list[float]:
    n = len(docs)
    lengths = [sum(d.values()) for d in docs]
    average = (sum(lengths) / n) or 1.0
    df = Counter(t for d in docs for t in d if t in weights)
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    out = []
    for doc, length in zip(docs, lengths, strict=True):
        norm = _K1 * (1 - _B + _B * length / average)
        out.append(
            sum(w * idf[t] * doc[t] * (_K1 + 1) / (doc[t] + norm) for t, w in weights.items() if t in doc)
        )
    return out


def _scale(raw: float) -> float:
    return FLOOR + (1 - FLOOR) * raw / (raw + _HALF) if raw > 0 else FLOOR


__all__ = ["FLOOR", "query_weights", "ranked_candidates", "terms"]
