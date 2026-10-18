"""RED → GREEN: two attribute profiles + mutual exclusion (Phase1 §3.4)."""

from __future__ import annotations

import pytest

from supervisor.telemetry_contract.spans import SpanRecord, SpanStatus
from supervisor.telemetry_contract.validate import ValidationError, validate_span


def _base(**kwargs: object) -> SpanRecord:
    defaults: dict[str, object] = {
        "name": "chat",
        "yal_tenant_id": "t1",
        "yal_session_id": "s1",
        "yal_run_id": "r1",
        "yal_harness_id": "coding",
        "yal_harness_version": "coding-v1",
        "attributes": {},
        "status": SpanStatus.UNSET,
    }
    defaults.update(kwargs)
    return SpanRecord(**defaults)  # type: ignore[arg-type]


def test_genai_chat_requires_provider_and_operation() -> None:
    span = _base(
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.usage.input_tokens": 1811,
            "gen_ai.usage.output_tokens": 96,
            "gen_ai.usage.cache_read.input_tokens": 0,
            "gen_ai.usage.cache_creation.input_tokens": 1801,
        }
    )
    validate_span(span)


def test_platform_turn_forbids_genai_operation_and_provider() -> None:
    span = _base(
        name="yal.turn",
        attributes={"yal.operation.name": "turn"},
    )
    validate_span(span)

    bad = _base(
        name="yal.turn",
        attributes={
            "yal.operation.name": "turn",
            "gen_ai.operation.name": "chat",
        },
    )
    with pytest.raises(ValidationError, match="mutual"):
        validate_span(bad)

    bad_provider = _base(
        name="yal.turn",
        attributes={
            "yal.operation.name": "turn",
            "gen_ai.provider.name": "yal",
        },
    )
    with pytest.raises(ValidationError, match="provider"):
        validate_span(bad_provider)


def test_genai_and_yal_operation_mutually_exclusive() -> None:
    span = _base(
        attributes={
            "gen_ai.operation.name": "chat",
            "yal.operation.name": "turn",
            "gen_ai.provider.name": "anthropic",
        }
    )
    with pytest.raises(ValidationError, match="mutual"):
        validate_span(span)


def test_chat_token_nesting_enforced() -> None:
    """input_tokens must be >= cache_read + cache_write."""
    span = _base(
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.usage.input_tokens": 10,  # not summed — invalid
            "gen_ai.usage.output_tokens": 96,
            "gen_ai.usage.cache_read.input_tokens": 0,
            "gen_ai.usage.cache_creation.input_tokens": 1801,
        }
    )
    with pytest.raises(ValidationError, match="nest"):
        validate_span(span)


def test_no_sandbox_operation_in_phase1_enum_use() -> None:
    """Phase 1: sandbox ops reserved / not projected — validator rejects emission."""
    span = _base(
        name="yal.sandbox.create",
        attributes={"yal.operation.name": "sandbox.create"},
    )
    with pytest.raises(ValidationError, match="sandbox"):
        validate_span(span)
