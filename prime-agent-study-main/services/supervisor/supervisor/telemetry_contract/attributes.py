"""prime.* attribute names and cardinality classes (Phase1 §3.5–3.6)."""

from __future__ import annotations

LOW = "low"
HIGH = "high"

CARDINALITY: dict[str, str] = {
    "gen_ai.operation.name": LOW,
    "prime.operation.name": LOW,
    "gen_ai.provider.name": LOW,
    "gen_ai.request.model": LOW,
    "gen_ai.agent.name": LOW,
    "prime.harness.id": LOW,
    "prime.harness.version": LOW,
    "prime.environment": LOW,
    "prime.tool.outcome": LOW,
    "prime.outcome": LOW,
    "prime.closed_by": LOW,
    "error.type": LOW,
    "prime.cost.currency": LOW,
    "prime.tenant.id": HIGH,
    "prime.session.id": HIGH,
    "prime.run.id": HIGH,
    "prime.turn.id": HIGH,
    "prime.journal.seq": HIGH,
    "tool_call_id": HIGH,
    "trace_id": HIGH,
    "span_id": HIGH,
}

# Common attribute key constants
PRIME_TENANT_ID = "prime.tenant.id"
PRIME_SESSION_ID = "prime.session.id"
PRIME_RUN_ID = "prime.run.id"
PRIME_TURN_ID = "prime.turn.id"
PRIME_JOURNAL_SEQ = "prime.journal.seq"
PRIME_HARNESS_ID = "prime.harness.id"
PRIME_HARNESS_VERSION = "prime.harness.version"
PRIME_OPERATION_NAME = "prime.operation.name"
PRIME_TOOL_OUTCOME = "prime.tool.outcome"
PRIME_TOOL_UNPAIRED = "prime.tool.unpaired"
PRIME_OUTCOME = "prime.outcome"
PRIME_CLOSED_BY = "prime.closed_by"
PRIME_COST_TOTAL = "prime.cost.total"
PRIME_COST_CURRENCY = "prime.cost.currency"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
