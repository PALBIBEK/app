"""Parse W3C traceparent (Phase1 §5.2a)."""

from __future__ import annotations

from supervisor.obs.trace_context import parse_traceparent


def test_parse_full_traceparent() -> None:
    tid = "0af7651916cd43dd8448eb211c80319c"
    header = f"00-{tid}-b7ad6b7169203331-01"
    assert parse_traceparent(header) == tid


def test_parse_bare_hex() -> None:
    tid = "a" * 32
    assert parse_traceparent(tid) == tid


def test_parse_invalid() -> None:
    assert parse_traceparent(None) is None
    assert parse_traceparent("not-a-trace") is None
