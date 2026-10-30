"""What a session keeps of one tool result is bounded, whatever the tool returns."""

from __future__ import annotations

import json

from supervisor.agent.runtimes.githi.render import bound
from supervisor.seams.core.types import ToolResult
from supervisor.session.manager_tools import MAX_KEPT_RESULT_CHARS, kept
from supervisor.tools.truncate import cut_middle


def test_a_result_within_bounds_is_kept_as_it_is() -> None:
    result = ToolResult(ok=True, payload={"entries": ["a", "b"]})
    assert kept(result) is result


def test_a_huge_text_keeps_its_head_and_tail() -> None:
    result = kept(ToolResult(ok=True, payload="start " + "x" * 1_000_000 + " end"))
    assert result.ok and result.payload.startswith("start ") and result.payload.endswith(" end")
    assert "characters cut" in result.payload
    assert len(result.payload) < MAX_KEPT_RESULT_CHARS + 100


def test_a_huge_structured_payload_is_kept_as_its_text() -> None:
    payload = {"content": [{"type": "text", "text": "y" * 500_000}], "first": True}
    result = kept(ToolResult(ok=True, payload=payload))
    assert isinstance(result.payload, str) and result.payload.startswith(json.dumps(payload)[:20])
    assert len(result.payload) < MAX_KEPT_RESULT_CHARS + 100


def test_a_huge_error_is_bounded_too() -> None:
    result = kept(ToolResult(ok=False, error="MCP HTTP 500: " + "z" * 400_000, code="MCP_ERROR"))
    assert result.error is not None and result.error.startswith("MCP HTTP 500: ")
    assert len(result.error) < MAX_KEPT_RESULT_CHARS + 100 and result.code == "MCP_ERROR"


def test_the_model_reads_the_same_cut_as_before() -> None:
    text = "a" * 100 + "b" * 100
    assert cut_middle(text, 50) == "a" * 25 + "\n[... 150 characters cut ...]\n" + "b" * 25
    assert bound(text, chars=50) == cut_middle(text, 50)
    assert cut_middle("short", 50) == "short"
