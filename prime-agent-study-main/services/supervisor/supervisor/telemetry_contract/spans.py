"""Span model — GenAI vs platform profiles (Phase1 §3.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SpanStatus(StrEnum):
    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


@dataclass
class SpanRecord:
    """Contract-shaped span. Content bodies must never appear in attributes."""

    name: str
    prime_tenant_id: str
    prime_session_id: str
    prime_run_id: str
    prime_harness_id: str
    prime_harness_version: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.UNSET
    parent_span_id: str | None = None
    span_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    journal_seq: int | None = None
