"""Static PromptAssembler — fixed text for tests / defaults."""

from __future__ import annotations

import hashlib

from supervisor.seams.core.types import AssembledPrompt, PromptBindings


class StaticPromptAssembler:
    """Provider: returns fixed system prompt text regardless of bindings."""

    def __init__(self, text: str, *, include_digest: bool = True) -> None:
        self._text = text
        self._include_digest = include_digest

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        digest = None
        if self._include_digest:
            digest = hashlib.sha256(self._text.encode("utf-8")).hexdigest()
        return AssembledPrompt(text=self._text, content_digest=digest)
