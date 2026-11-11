"""Sensitivity classification of captured and edited facts (QM's memory/classification).

A model estimates the label; any failure or malformed answer is ``unknown``. The
label only ever tightens what the caller already knew. Classification is an
estimate, not proof of harmlessness.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory.view import MemoryView
from supervisor.memory_service.extraction_prompt import SENSITIVITY_PROMPT
from supervisor.memory_service.notebooks.types import CaptureMetadata, Edit, Sensitivity, stricter
from supervisor.memory_service.observations import (
    MAX_MODEL_BYTES,
    normalize_term,
)

logger = logging.getLogger(__name__)

# (system prompt, user content) -> model text. Blocking callers wrap this in a thread.
type OneShot = Callable[[str, str], Awaitable[str]]

DEFAULT_MODEL = "platform-default"
"""What a one-shot that does not name its model is recorded as (the platform gateway's
default route)."""


def one_shot_model(one_shot: OneShot) -> str:
    """The model ``one_shot`` calls, for provenance (a one-shot may say, as
    ``platform.gateway_one_shot``'s do, with ``model``); ``DEFAULT_MODEL`` when it does not."""
    model = getattr(one_shot, "model", None)
    if not model:
        return DEFAULT_MODEL
    # As the store bounds it (``observations``).
    return normalize_term(str(model), MAX_MODEL_BYTES) or DEFAULT_MODEL



async def classify(one_shot: OneShot, texts: Sequence[str]) -> Sensitivity:
    try:
        answer = await one_shot(
            f"{SENSITIVITY_PROMPT}\nOutput ONLY one label for the entire list.",
            json.dumps(list(texts)),
        )
    except Exception:  # a failed estimate is "unknown", never a crash
        logger.warning("memory classification failed", exc_info=True)
        return Sensitivity.UNKNOWN
    return Sensitivity.parse(answer)


class ClassifiedMemory(MemoryView):
    """Labels explicit captures and every edit; automatic captures arrive pre-labelled."""

    def __init__(self, base: MemoryService, one_shot: OneShot) -> None:
        super().__init__(base)
        self._one_shot = one_shot

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int:
        context = context or CaptureContext()
        sensitivity = context.sensitivity
        if context.mode != "automatic" or sensitivity is None:
            sensitivity = stricter(sensitivity, await classify(self._one_shot, facts))
        return await self._base.capture(
            notebook_id,
            facts,
            author=author,
            context=_with_sensitivity(context, sensitivity),
        )

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        context = context or CaptureMetadata()
        sensitivity = stricter(
            context.sensitivity, await classify(self._one_shot, [e.text for e in edits])
        )
        await self._base.edit(
            notebook_id, edits, author=author, context=_with_sensitivity(context, sensitivity)
        )


def _with_sensitivity[C: CaptureMetadata](context: C, sensitivity: Sensitivity) -> C:
    return replace(context, sensitivity=sensitivity)
