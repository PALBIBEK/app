"""Parse harness get_state data into AgentState.

``settled`` is computed on AgentState — never stored. No sessionFile field.
"""

from __future__ import annotations

from typing import Any

from supervisor.agent.base import AgentPhase, AgentState


def from_get_state(payload: dict[str, Any]) -> AgentState:
    model = payload.get("model") or {}
    model_id = model.get("id") if isinstance(model, dict) else None
    context_window = model.get("contextWindow") if isinstance(model, dict) else None
    # AgentState derives is_busy / is_compacting from its phase.
    if payload.get("isCompacting"):
        phase = AgentPhase.COMPACTING
    elif payload.get("isStreaming"):
        phase = AgentPhase.EXECUTING
    else:
        phase = AgentPhase.READY
    return AgentState(
        session_id=str(payload.get("sessionId") or ""),
        phase=phase,
        pending_count=int(payload.get("pendingMessageCount") or 0),
        message_count=int(payload.get("messageCount") or 0),
        model=str(model_id) if model_id is not None else None,
        context_window=int(context_window) if context_window is not None else None,
    )
