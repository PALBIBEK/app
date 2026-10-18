"""Future AGI export adapter — fi.span.kind only (Phase1 §3.1)."""

from __future__ import annotations

from supervisor.telemetry_contract.operations import GenAIOperation

_FI_KIND: dict[str, str] = {
    GenAIOperation.INVOKE_AGENT: "AGENT",
    GenAIOperation.CHAT: "LLM",
    GenAIOperation.EXECUTE_TOOL: "TOOL",
    GenAIOperation.SEARCH_MEMORY: "RETRIEVER",
    GenAIOperation.EMBEDDINGS: "EMBEDDING",
    GenAIOperation.INVOKE_WORKFLOW: "CHAIN",
    GenAIOperation.PLAN: "CHAIN",
}


def fi_span_kind(attributes: dict) -> str | None:
    """Map GenAI/platform ops → FI kind. Platform ops → CHAIN."""
    gen_op = attributes.get("gen_ai.operation.name")
    if gen_op is not None:
        return _FI_KIND.get(str(gen_op), "UNKNOWN")
    if attributes.get("yal.operation.name") is not None:
        return "CHAIN"
    return None


def apply_fi_adapter(attributes: dict) -> dict:
    """Return a copy with fi.span.kind added; yal.* preserved."""
    out = dict(attributes)
    kind = fi_span_kind(attributes)
    if kind is not None:
        out["fi.span.kind"] = kind
    return out
