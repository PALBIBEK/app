"""Shared trace-shape assertions for in-memory and live OTLP e2e."""

from __future__ import annotations

from typing import Any


def _attrs(s: Any) -> dict[str, Any]:
    if hasattr(s, "attributes"):
        return dict(s.attributes or {})
    if isinstance(s, dict):
        tags = s.get("tags") or s.get("attributes") or {}
        if isinstance(tags, list):
            out: dict[str, Any] = {}
            for t in tags:
                key = t.get("key")
                if key is None:
                    continue
                val = t.get("value")
                if isinstance(val, dict) and "stringValue" in val:
                    val = val["stringValue"]
                elif isinstance(val, dict) and "intValue" in val:
                    val = int(val["intValue"])
                out[key] = val
            return out
        return dict(tags)
    return {}


def _name(s: Any) -> str:
    return str(getattr(s, "name", None) or (s.get("operationName") if isinstance(s, dict) else None) or "")


def _span_id(s: Any) -> str | None:
    ctx = getattr(s, "context", None)
    if ctx is not None:
        return format(ctx.span_id, "016x")
    if isinstance(s, dict):
        raw = s.get("spanID") or s.get("span_id")
        return str(raw).lower() if raw is not None else None
    return None


def _parent_id(s: Any) -> str | None:
    parent = getattr(s, "parent", None)
    if parent is not None:
        return format(parent.span_id, "016x")
    if isinstance(s, dict):
        for r in s.get("references") or []:
            if str(r.get("refType", "")).upper() in ("CHILD_OF", "CHILD OF"):
                return str(r.get("spanID") or "").lower()
        raw = s.get("parentSpanId") or s.get("parent_span_id")
        if raw:
            return str(raw).lower()
    return None


def _norm_id(x: str | None) -> str | None:
    if x is None:
        return None
    return str(x).lower().lstrip("0") or "0"


def assert_trace_shape(
    spans: list[Any],
    *,
    session_id: str,
    trace_id: str,
    secret_strings: list[str],
) -> None:
    """``spans`` are OTel ReadableSpans or Jaeger query span dicts."""
    assert spans, "expected at least one span"

    roots = [
        s
        for s in spans
        if "invoke_agent" in _name(s) and _norm_id(_parent_id(s)) in (None, "0", "")
    ]
    assert roots, "missing root invoke_agent"
    root = roots[0]
    a = _attrs(root)
    assert a.get("yal.run.id") == trace_id
    ctx = getattr(root, "context", None)
    if ctx is not None:
        assert format(ctx.trace_id, "032x") == trace_id

    turn = next(s for s in spans if _name(s) == "yal.turn")
    chat = next(s for s in spans if _name(s) == "chat")
    assert _norm_id(_parent_id(turn)) == _norm_id(_span_id(root))
    assert _norm_id(_parent_id(chat)) == _norm_id(_span_id(turn))

    chat_a, turn_a = _attrs(chat), _attrs(turn)
    assert chat_a.get("gen_ai.provider.name")
    assert turn_a.get("gen_ai.provider.name") is None
    assert turn_a.get("yal.operation.name") == "turn"
    assert chat_a.get("yal.operation.name") is None
    assert chat_a.get("gen_ai.operation.name") == "chat"

    for s in spans:
        sa = _attrs(s)
        if sa.get("gen_ai.operation.name") and sa.get("yal.operation.name"):
            raise AssertionError(f"mutual exclusion violated on {_name(s)}")

    if "gen_ai.usage.input_tokens" in chat_a:
        assert int(chat_a["gen_ai.usage.input_tokens"]) >= int(
            chat_a.get("gen_ai.usage.cache_creation.input_tokens") or 0
        ) + int(chat_a.get("gen_ai.usage.cache_read.input_tokens") or 0)

    blob = repr([_attrs(s) for s in spans])
    for secret in secret_strings:
        assert secret not in blob, f"content leaked into spans: {secret!r}"

    assert chat_a.get("yal.session.id") == session_id
    assert chat_a.get("yal.journal.seq") is not None
