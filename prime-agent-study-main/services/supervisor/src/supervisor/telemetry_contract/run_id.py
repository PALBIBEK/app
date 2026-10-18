"""yal.run.id resolver (Phase1 §3.3a).

Duck-typed: no import of supervisor.agent.events (contract stays extractable).
"""

from __future__ import annotations

from typing import Any, Protocol


class _EnvelopeLike(Protocol):
    session_id: str
    seq: int
    trace_id: str | None
    payload: Any


def resolve_run_id(env: _EnvelopeLike, *, explicit_run_id: str | None = None) -> str:
    """Prefer explicit run_id (subagent payload); else Envelope.trace_id; else session:seq."""
    if explicit_run_id:
        return explicit_run_id
    payload = getattr(env, "payload", None)
    rid = getattr(payload, "run_id", None) if payload is not None else None
    if rid:
        return str(rid)
    if env.trace_id:
        return env.trace_id
    return f"{env.session_id}:{env.seq}"
