"""yal.* attribute names and cardinality classes (Phase1 §3.5–3.6)."""

from __future__ import annotations

LOW = "low"
HIGH = "high"

CARDINALITY: dict[str, str] = {
    "gen_ai.operation.name": LOW,
    "yal.operation.name": LOW,
    "gen_ai.provider.name": LOW,
    "gen_ai.request.model": LOW,
    "gen_ai.agent.name": LOW,
    "yal.harness.id": LOW,
    "yal.harness.version": LOW,
    "yal.environment": LOW,
    "yal.tool.outcome": LOW,
    "yal.outcome": LOW,
    "yal.closed_by": LOW,
    "error.type": LOW,
    "yal.cost.currency": LOW,
    "yal.tenant.id": HIGH,
    "yal.session.id": HIGH,
    "yal.run.id": HIGH,
    "yal.turn.id": HIGH,
    "yal.journal.seq": HIGH,
    "tool_call_id": HIGH,
    "trace_id": HIGH,
    "span_id": HIGH,
}

# Common attribute key constants
YAL_TENANT_ID = "yal.tenant.id"
YAL_SESSION_ID = "yal.session.id"
YAL_RUN_ID = "yal.run.id"
YAL_TURN_ID = "yal.turn.id"
YAL_JOURNAL_SEQ = "yal.journal.seq"
YAL_HARNESS_ID = "yal.harness.id"
YAL_HARNESS_VERSION = "yal.harness.version"
YAL_OPERATION_NAME = "yal.operation.name"
YAL_TOOL_OUTCOME = "yal.tool.outcome"
YAL_TOOL_UNPAIRED = "yal.tool.unpaired"
YAL_OUTCOME = "yal.outcome"
YAL_CLOSED_BY = "yal.closed_by"
YAL_COST_TOTAL = "yal.cost.total"
YAL_COST_CURRENCY = "yal.cost.currency"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
