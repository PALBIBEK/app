"""Loop / stall guard unit tests."""

from __future__ import annotations

from supervisor.agent.events import ToolStart
from supervisor.policy.guards import call_hash


def _tool(name: str, args: dict, cid: str = "c1") -> ToolStart:
    return ToolStart(tool_call_id=cid, tool_name=name, args=args)








def test_canonical_args_hash_same() -> None:
    h1 = call_hash("bash", {"b": 2, "a": 1, "msg": "hello   world"})
    h2 = call_hash("bash", {"a": 1, "b": 2, "msg": "hello world"})
    assert h1 == h2








