"""Tier 1 extraction with a model, as memory systems do it (not with patterns).

mem0 (mem0ai/mem0, mem0/memory/main.py ``add`` with FACT_RETRIEVAL_PROMPT / the
additive extraction prompt), Graphiti (graphiti_core/prompts/extract_edges.py) and
LangMem (knowledge/extraction.py) all have a model read the conversation and state the
facts worth keeping. The regular expressions this replaced matched "no" and "I want",
so "I have no idea what you mean" became a 0.95-confidence correction.

The prompt and parser are QM's, the same the supervisor uses for notebooks
(``supervisor.memory_service.extraction_prompt``). The model is any OpenAI-compatible
chat-completions endpoint, normally the platform's model gateway:

- ``MEMORY_EXTRACTION_URL``: its base URL (``…/v1``)
- ``MEMORY_EXTRACTION_MODEL``: the model to ask
- ``MEMORY_EXTRACTION_TOKEN``: its bearer token, if it needs one

Without a URL and model there is no extraction: guessing facts is worse than none.
Only facts the model labels ``ordinary`` reach the bank, whose recall applies no
audience rules (notebooks bank the same way, ``notebooks.store.bankable``).
A failed call raises, so the job is retried with backoff (``extract.ExtractionQueue``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from supervisor.memory_service.extract import ExtractedCandidate
from supervisor.memory_service.extraction_prompt import (
    MEMORY_EXTRACTION_PROMPT,
    Exchange,
    parse_extraction,
    transcript,
)
from supervisor.memory_service.models import SessionEventRow
from supervisor.memory_service.notebooks.types import Sensitivity

EXTRACTED_CONFIDENCE = 0.8
"""A model-extracted fact's confidence: NewMemoryItem's default, the same for every one."""


class ModelExtractor:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout_s)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> ModelExtractor | None:
        url = environ.get("MEMORY_EXTRACTION_URL", "").strip()
        model = environ.get("MEMORY_EXTRACTION_MODEL", "").strip()
        if not url or not model:
            return None
        return cls(url, model=model, token=environ.get("MEMORY_EXTRACTION_TOKEN") or None)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def extract(self, events: Sequence[SessionEventRow]) -> list[ExtractedCandidate]:
        exchanges, last_seq = _exchanges(events)
        if not exchanges:
            return []
        response = await self._http.post(
            self._url,
            headers=self._headers,
            json={
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": MEMORY_EXTRACTION_PROMPT},
                    {"role": "user", "content": transcript(exchanges)},
                ],
            },
        )
        response.raise_for_status()
        extracted = parse_extraction(_content(response.json()))
        if extracted.sensitivity is not Sensitivity.ORDINARY:
            return []  # the bank shows items to anyone in scope
        return [
            ExtractedCandidate(content=fact, confidence=EXTRACTED_CONFIDENCE, source_seq=last_seq, kind="extracted")
            for fact in extracted.facts
        ]


def _text(event: SessionEventRow) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    return str(data.get("text") or data.get("content") or "").strip()


def _exchanges(events: Sequence[SessionEventRow]) -> tuple[list[Exchange], int]:
    """User messages, each with the assistant's replies after it, and the last seq read."""
    out: list[Exchange] = []
    said, last = "", -1
    replied: list[str] = []
    for event in events:
        if event.type == "user/message" and (text := _text(event)):
            if said:
                out.append(Exchange(said, "\n".join(replied)))
            said, replied, last = text, [], event.seq
        elif event.type == "assistant/message" and said and (text := _text(event)):
            replied.append(text)
            last = event.seq
    if said:
        out.append(Exchange(said, "\n".join(replied)))
    return out, last


def _content(body: Any) -> str:
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""
