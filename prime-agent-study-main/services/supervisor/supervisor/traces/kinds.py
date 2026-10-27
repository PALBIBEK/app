"""What a span is: one small set of kinds, mapped to the outside conventions in one place.

The UI renders by kind (a model call as chat, a tool as arguments and result, an
approval as a decision), so every span has exactly one. Exporters translate a kind
to OpenTelemetry GenAI operation names and OpenInference span kinds through
``EXPORT``; nothing else in the code spells those names.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["EXPORT", "Export", "SpanKind"]


class SpanKind(StrEnum):
    AGENT = "agent"
    """A run of an agent: the trace root, or a subagent inside it."""
    STEP = "step"
    """One pass of the agent loop: a model call and the tools it asked for."""
    LLM = "llm"
    """One model call through the gateway."""
    TOOL = "tool"
    """One tool call, from the model asking for it to its result (approval included)."""
    APPROVAL = "approval"
    """A person deciding whether a tool call may run. Its time is human time, not latency."""
    MEMORY = "memory"
    """Memory recalled into the context."""
    COMPACTION = "compaction"
    """The context compacted to fit the window."""
    JOB = "job"
    """A background job the run started."""


@dataclass(frozen=True)
class Export:
    genai_operation: str | None
    """``gen_ai.operation.name`` (OpenTelemetry GenAI conventions); None where they define none."""
    platform_operation: str | None
    """``yal.operation.name`` for what the conventions do not cover."""
    openinference: str
    """``openinference.span.kind``."""


EXPORT: dict[SpanKind, Export] = {
    SpanKind.AGENT: Export("invoke_agent", None, "AGENT"),
    SpanKind.STEP: Export(None, "step", "CHAIN"),
    SpanKind.LLM: Export("chat", None, "LLM"),
    SpanKind.TOOL: Export("execute_tool", None, "TOOL"),
    SpanKind.APPROVAL: Export(None, "approval", "GUARDRAIL"),
    SpanKind.MEMORY: Export("search_memory", None, "RETRIEVER"),
    SpanKind.COMPACTION: Export(None, "compaction", "CHAIN"),
    SpanKind.JOB: Export(None, "job", "CHAIN"),
}
