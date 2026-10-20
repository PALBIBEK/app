"""The session as a context object: search, slices, tool results and calls, bounded (BH7)."""

from __future__ import annotations

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, ToolStart, TurnStart
from supervisor.memory import history
from supervisor.temporal.context_memory import _replaced_range


def _journal(*payloads: object) -> list[Envelope]:
    return [
        Envelope("s1", seq, f"e{seq}", 1_790_000_000_000 + seq, "run-1", None, payload)  # type: ignore[arg-type]
        for seq, payload in enumerate(payloads)
    ]


def _say(role: str, text: str) -> MessageEnd:
    return MessageEnd(role=role, content=[{"type": "text", "text": text}])


EVENTS = _journal(
    TurnStart(),
    _say("user", "read the config please"),
    ToolStart(tool_call_id="c1", tool_name="read", args={"path": "config.yaml"}),
    ToolEnd(tool_call_id="c1", tool_name="read", result={"ok": True, "payload": "port: 8080\nhost: db"}),
    _say("assistant", "the port is 8080"),
    ToolStart(tool_call_id="c2", tool_name="bash", args={"command": "false"}),
    ToolEnd(tool_call_id="c2", tool_name="bash", result={"ok": False, "error": "exit 1"}, is_error=True),
)


def test_search_skips_tool_results_but_opening_returns_them_verbatim() -> None:
    assert history.search(EVENTS, "8080") == [history.search(EVENTS, "port is")[0]]  # the reply only
    opened = history.open_entry(EVENTS, 3)
    assert opened is not None and opened.startswith("tool_result#3") and opened.endswith("port: 8080\nhost: db")
    assert history.open_entry(EVENTS, 0) is None  # bookkeeping is not an entry


def test_a_slice_reads_a_range_in_order_with_errors_marked() -> None:
    sliced = history.slice_entries(EVENTS, 2, 6).splitlines()
    kinds = [line.split("#", 1)[0] for line in sliced if "#" in line and not line.startswith(("port", "host"))]
    assert kinds == ["tool_call", "tool_result", "assistant", "tool_call", "tool_error"]
    assert history.slice_entries(EVENTS, 100) == ""


def test_a_slice_is_bounded_and_says_where_to_continue() -> None:
    many = _journal(*(_say("user", f"message {n} " + "x" * 600) for n in range(80)))
    sliced = history.slice_entries(many, 0)
    assert len(sliced) <= history.MAX_SLICE_CHARS + 200
    assert "(open #0)" in sliced  # each entry clipped, with a pointer to all of it
    assert "[more entries follow: continue with from_seq=" in sliced.splitlines()[-1]


def test_a_call_comes_back_with_its_result() -> None:
    found = history.call_entries(EVENTS, "c1")
    assert found is not None and found.splitlines()[0].startswith("tool_call#2") and "host: db" in found
    assert history.call_entries(EVENTS, "nope") is None


def test_a_compaction_names_the_entries_before_the_kept_tail() -> None:
    tail = [{"role": "assistant", "content": "the port is 8080"}]
    assert _replaced_range(EVENTS, tail) == (1, 3)
    assert _replaced_range(EVENTS, [{"role": "user", "content": "unseen"}]) == (1, 6)  # all of it
    assert _replaced_range(_journal(TurnStart()), tail) is None
