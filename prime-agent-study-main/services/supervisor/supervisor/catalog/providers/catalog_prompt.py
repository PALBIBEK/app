"""Catalog-backed PromptAssembler — assemble from a pinned PromptPack."""

from __future__ import annotations

import hashlib

from supervisor.catalog.core.types import PromptPack
from supervisor.seams import AssembledPrompt, PromptBindings


class CatalogPromptAssembler:
    """Provider: deterministic system prompt from pinned PromptPack sections."""

    def __init__(self, pack: PromptPack) -> None:
        self._pack = pack

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        parts: list[str] = []
        if self._pack.persona and bindings.include_persona:
            parts.append(self._pack.persona)
        for section in sorted(self._pack.sections, key=lambda s: (s.order, s.id)):
            text = section.text
            if "cwd" in self._pack.vars and "{cwd}" in text:
                text = text.replace("{cwd}", bindings.cwd)
            parts.append(text)
        if bindings.extra:
            parts.append(bindings.extra)
        if bindings.custom:
            parts.append(bindings.custom)
        body = "\n\n".join(parts)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return AssembledPrompt(text=body, content_digest=f"sha256:{digest}")
