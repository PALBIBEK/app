"""Span validator — two profiles, mutual exclusion, token nesting (Phase1 §3.4/§3.7)."""

from __future__ import annotations

from supervisor.telemetry_contract.operations import (
    PHASE1_FORBIDDEN_PLATFORM_OPS,
    GenAIOperation,
)
from supervisor.telemetry_contract.spans import SpanRecord


class ValidationError(ValueError):
    pass


_GEN_AI_OPS = {m.value for m in GenAIOperation}


def validate_span(span: SpanRecord) -> None:
    attrs = span.attributes
    gen_op = attrs.get("gen_ai.operation.name")
    yal_op = attrs.get("yal.operation.name")

    if gen_op is not None and yal_op is not None:
        raise ValidationError("mutual exclusion: both gen_ai.operation.name and yal.operation.name")

    if yal_op is not None:
        if yal_op in PHASE1_FORBIDDEN_PLATFORM_OPS or str(yal_op).startswith("sandbox"):
            raise ValidationError(f"sandbox ops not projected in Phase 1: {yal_op}")
        if "gen_ai.provider.name" in attrs:
            raise ValidationError("provider must be absent on platform spans")
        if "gen_ai.operation.name" in attrs:
            raise ValidationError("mutual exclusion violated")
        return

    if gen_op is None:
        raise ValidationError("span must carry gen_ai.operation.name or yal.operation.name")

    if gen_op not in _GEN_AI_OPS:
        raise ValidationError(f"unknown gen_ai.operation.name: {gen_op}")

    if "gen_ai.provider.name" not in attrs:
        raise ValidationError("gen_ai.provider.name required on GenAI spans")

    if gen_op == GenAIOperation.CHAT:
        _assert_token_nesting(attrs)


def _assert_token_nesting(attrs: dict) -> None:
    inp = attrs.get("gen_ai.usage.input_tokens")
    cr = attrs.get("gen_ai.usage.cache_read.input_tokens", 0) or 0
    cw = attrs.get("gen_ai.usage.cache_creation.input_tokens", 0) or 0
    if inp is None:
        return
    if int(inp) < int(cr) + int(cw):
        raise ValidationError(
            f"token nesting violated: input_tokens={inp} < cache_read+cache_write={cr + cw}"
        )
