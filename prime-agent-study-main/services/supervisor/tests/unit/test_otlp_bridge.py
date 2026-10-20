"""In-memory OTLP bridge assertions (OTLP-bridge.md §1.4 — no Jaeger required)."""

from __future__ import annotations

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.tracing import make_inmemory_otlp_projector

SECRET_BODY = "SECRET_PROMPT_BODY_XYZ"
SECRET_PATH = "/super/secret/path.txt"
TRACE = "a" * 32


def _env(seq: int, payload: object, *, turn_id: str | None = None) -> Envelope:
    return Envelope(
        session_id="s1",
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_000 + seq,
        turn_id=turn_id,
        trace_id=TRACE,
        payload=payload,  # type: ignore[arg-type]
    )


def test_echo_shaped_run_in_memory_otlp() -> None:
    proj, exporter = make_inmemory_otlp_projector()
    turn = "turn-1"
    for e in [
        _env(0, RunStart()),
        _env(1, TurnStart(), turn_id=turn),
        _env(
            2,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": SECRET_BODY}],
                usage=Usage(10, 96, 0, 1801, 0.008),
            ),
            turn_id=turn,
        ),
        _env(
            3,
            ToolStart(
                tool_call_id="c1",
                tool_name="read",
                args={"path": SECRET_PATH},
            ),
            turn_id=turn,
        ),
        _env(
            4,
            ToolEnd(
                tool_call_id="c1",
                tool_name="read",
                result={"ok": True, "text": SECRET_BODY},
                is_error=False,
            ),
            turn_id=turn,
        ),
        _env(5, TurnEnd(stop_reason="end"), turn_id=turn),
        _env(6, RunEnd(messages=[{"role": "user", "content": SECRET_BODY}])),
    ]:
        proj.project(e)

    spans = list(exporter.get_finished_spans())
    assert spans

    by_name = {s.name: s for s in spans}
    root = next(s for s in spans if "invoke_agent" in s.name)
    assert root.attributes.get("yal.run.id") == TRACE
    # TraceId matches yal.run.id (§3.3a)
    assert format(root.context.trace_id, "032x") == TRACE

    turn_span = by_name["yal.turn"]
    chat = by_name["chat"]
    next(s for s in spans if s.name.startswith("execute_tool"))

    assert turn_span.parent is not None
    assert turn_span.parent.span_id == root.context.span_id
    assert chat.parent is not None
    assert chat.parent.span_id == turn_span.context.span_id

    # Profiles
    assert chat.attributes.get("gen_ai.provider.name")
    assert turn_span.attributes.get("gen_ai.provider.name") is None
    assert turn_span.attributes.get("yal.operation.name") == "turn"
    assert chat.attributes.get("yal.operation.name") is None
    assert chat.attributes.get("gen_ai.operation.name") == "chat"

    # Token nesting
    assert chat.attributes.get("gen_ai.usage.input_tokens") == 10 + 1801

    # Content absent
    blob = repr([dict(s.attributes) for s in spans])
    assert SECRET_BODY not in blob
    assert SECRET_PATH not in blob

    # Deep-link coords
    assert chat.attributes.get("yal.session.id") == "s1"
    assert chat.attributes.get("yal.journal.seq") == 2


def test_otlp_setup_without_endpoint_is_noop_logging() -> None:
    import os

    from supervisor.obs import tracing as tracing_mod

    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    tracing_mod._projector = None
    proj = tracing_mod.setup_tracing()
    assert proj is not None
    # Logging sink — projecting must not raise
    proj.project(
        Envelope(
            session_id="s",
            seq=0,
            event_id="e",
            ts_ms=1,
            turn_id=None,
            trace_id=TRACE,
            payload=RunStart(),
        )
    )
