"""What a session keeps of one tool result is bounded, whatever the tool returns."""

from __future__ import annotations

import json

from supervisor.agent.runtimes.githi.render import bound
from supervisor.seams.core.types import ToolResult
from supervisor.session.manager_tools import MAX_KEPT_IMAGE_CHARS, MAX_KEPT_RESULT_CHARS, kept
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


def test_images_are_kept_beside_the_text_not_cut_as_part_of_it() -> None:
    """A screenshot is larger than the text bound: it is measured on its own, and of
    images past their own bound the latest are kept."""
    shot = {"mime_type": "image/jpeg", "data": "A" * (MAX_KEPT_IMAGE_CHARS // 3)}
    one = ToolResult(ok=True, payload={"output": "seen", "images": [shot]})
    assert kept(one) is one

    newest = {"mime_type": "image/jpeg", "data": "B" * (MAX_KEPT_IMAGE_CHARS // 3)}
    many = kept(ToolResult(ok=True, payload={"output": "x" * 500_000, "images": [shot, shot, shot, newest]}))
    assert isinstance(many.payload, str), "the text is cut as before, and an image is not text"
    several = kept(ToolResult(ok=True, payload={"output": "seen", "images": [shot, shot, shot, newest]}))
    assert several.payload["images"] == [shot, shot, newest]
