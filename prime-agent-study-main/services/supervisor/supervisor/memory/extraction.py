"""Automatic capture: extract durable facts from a burst of exchanges (QM per-turn strategy).

The prompt is QM's, verbatim (``supervisor.memory_service.extraction_prompt``, shared
with the memory service's own extraction). The model returns bullet facts (or NONE) and
a sensitivity label for the whole list; a failure or malformed answer extracts nothing
and labels "unknown", so capture never invents provenance.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from supervisor.memory.classification import OneShot
from supervisor.memory_service.extraction_prompt import (
    MEMORY_EXTRACTION_PROMPT,
    Exchange,
    Extracted,
    parse_extraction,
    parse_facts,
    transcript,
)
from supervisor.memory_service.notebooks.types import Sensitivity

__all__ = ["MEMORY_EXTRACTION_PROMPT", "Exchange", "Extracted", "extract_facts", "parse_facts", "transcript"]

logger = logging.getLogger(__name__)


async def extract_facts(one_shot: OneShot, turns: Sequence[Exchange]) -> Extracted:
    try:
        out = await one_shot(MEMORY_EXTRACTION_PROMPT, transcript(turns))
    except Exception:  # a failed extraction remembers nothing, never crashes
        logger.warning("memory extraction failed", exc_info=True)
        return Extracted([], Sensitivity.UNKNOWN)
    return parse_extraction(out or "")
