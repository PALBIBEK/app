"""Default PromptAssembler wrapping build_system_prompt."""

from __future__ import annotations

import hashlib

from ...harness.system_prompt import build_system_prompt
from ..prompt_assembler import AssembledPrompt, PromptBindings


class DefaultPromptAssembler:
    """Provider: byte-compatible wrapper around build_system_prompt."""

    def __init__(self, *, include_digest: bool = False) -> None:
        self.include_digest = include_digest

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        text = build_system_prompt(
            cwd=bindings.cwd,
            tools=bindings.tools,
            skill_listing=bindings.skill_listing,
            extra=bindings.extra,
            custom=bindings.custom,
        )
        digest = None
        if self.include_digest:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return AssembledPrompt(text=text, content_digest=digest)
