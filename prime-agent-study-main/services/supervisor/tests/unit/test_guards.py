"""Loop / stall guard unit tests."""

from __future__ import annotations

from supervisor.agent.events import MessageEnd, RunEnd, ToolStart, TurnStart
from supervisor.policy.guards import GuardAction, GuardConfig, LoopGuard, call_hash


def _tool(name: str, args: dict, cid: str = "c1") -> ToolStart:
    return ToolStart(tool_call_id=cid, tool_name=name, args=args)


def test_three_identical_trips_repetition() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=100))
    g.observe("s", TurnStart())
    assert not g.observe("s", _tool("bash", {"cmd": "ls"}, "a")).tripped
    assert not g.observe("s", _tool("bash", {"cmd": "ls"}, "b")).tripped
    v = g.observe("s", _tool("bash", {"cmd": "ls"}, "c"))
    assert v.tripped
    assert v.detector == "repetition"
    assert "bash" in (v.detail or "")


def test_two_identical_then_different_does_not_trip() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=100))
    g.observe("s", _tool("bash", {"cmd": "ls"}, "a"))
    g.observe("s", _tool("bash", {"cmd": "ls"}, "b"))
    v = g.observe("s", _tool("bash", {"cmd": "pwd"}, "c"))
    assert not v.tripped


def test_oscillation_ab_ab() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=100))
    assert not g.observe("s", _tool("a", {"x": 1}, "1")).tripped
    assert not g.observe("s", _tool("b", {"y": 2}, "2")).tripped
    assert not g.observe("s", _tool("a", {"x": 1}, "3")).tripped
    v = g.observe("s", _tool("b", {"y": 2}, "4"))
    assert v.tripped
    assert v.detector == "oscillation"


def test_canonical_args_hash_same() -> None:
    h1 = call_hash("bash", {"b": 2, "a": 1, "msg": "hello   world"})
    h2 = call_hash("bash", {"a": 1, "b": 2, "msg": "hello world"})
    assert h1 == h2


def test_stall_after_n_without_progress() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=3, repetition=GuardAction.OFF))
    for i in range(2):
        assert not g.observe("s", _tool(f"t{i}", {"i": i}, str(i))).tripped
    v = g.observe("s", _tool("t2", {"i": 2}, "2"))
    assert v.tripped
    assert v.detector == "stall"


def test_long_distinct_sequence_ok() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=10_000))
    for i in range(50):
        v = g.observe("s", _tool("t", {"i": i}, str(i)))
        assert not v.tripped
        if i % 5 == 4:
            g.observe("s", MessageEnd(role="assistant", content=[], usage=None))


def test_window_bounded() -> None:
    g = LoopGuard(window=8, config=GuardConfig(stall_iterations=10_000))
    for i in range(1000):
        g.observe("s", _tool("t", {"i": i}, str(i)))
    assert g.window_size("s") <= 8


def test_run_end_resets_stall() -> None:
    g = LoopGuard(config=GuardConfig(stall_iterations=3, repetition=GuardAction.OFF))
    g.observe("s", _tool("a", {}, "1"))
    g.observe("s", _tool("b", {}, "2"))
    g.observe("s", RunEnd(messages=[]))
    assert not g.observe("s", _tool("c", {}, "3")).tripped
