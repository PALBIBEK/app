"""Core message/tool types.

The wire format is the contract with the provider, so these tests pin the
exact dict shapes an OpenAI-compatible endpoint requires. A silently wrong
shape here surfaces as an opaque 400 from the provider, far from the cause.
"""

from __future__ import annotations

import pytest

from githi.errors import GithiError, ToolNotFound
from githi.types import (
    AssistantMessage,
    Message,
    Role,
    StopReason,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class TestMessages:
    def test_user_message_wire_shape(self):
        assert UserMessage("hello").to_wire() == {"role": "user", "content": "hello"}

    def test_system_message_wire_shape(self):
        assert SystemMessage("be brief").to_wire() == {"role": "system", "content": "be brief"}

    def test_assistant_text_only_omits_tool_calls(self):
        """`tool_calls: []` is not the same as absent -- some providers reject
        an empty array."""
        assert AssistantMessage(content="hi").to_wire() == {"role": "assistant", "content": "hi"}

    def test_thinking_replays_on_the_provider_field(self):
        wire = AssistantMessage(
            content="ok", thinking="plan", thinking_signature="reasoning",
        ).to_wire()
        assert wire["reasoning"] == "plan"
        assert "reasoning_content" not in wire

    def test_assistant_with_tool_calls(self):
        msg = AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})],
        )
        wire = msg.to_wire()
        assert wire["role"] == "assistant"
        assert wire["tool_calls"] == [{
            "id": "c1",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "ls"}'},
        }]

    def test_tool_arguments_are_json_encoded_not_dicts(self):
        """The OpenAI schema requires `arguments` be a JSON *string*."""
        wire = AssistantMessage(
            content="", tool_calls=[ToolCall(id="c", name="t", arguments={"a": 1})]
        ).to_wire()
        assert isinstance(wire["tool_calls"][0]["function"]["arguments"], str)

    def test_tool_result_wire_shape(self):
        assert ToolResultMessage(tool_call_id="c1", content="done").to_wire() == {
            "role": "tool", "tool_call_id": "c1", "content": "done",
        }

    def test_roles_are_the_four_openai_roles(self):
        assert {r.value for r in Role} == {"system", "user", "assistant", "tool"}

    def test_messages_are_comparable(self):
        """Session round-tripping and compaction both rely on equality."""
        assert UserMessage("x") == UserMessage("x")
        assert UserMessage("x") != UserMessage("y")


class TestToolCall:
    def test_parses_json_string_arguments(self):
        """Providers hand back a JSON string; callers want a dict."""
        assert ToolCall.from_wire({
            "id": "c1", "function": {"name": "bash", "arguments": '{"command": "ls"}'}
        }).arguments == {"command": "ls"}

    def test_malformed_arguments_do_not_raise(self):
        """A truncated tool-call payload is a known provider failure. It must
        become a normal failed tool result, not kill the run."""
        call = ToolCall.from_wire({"id": "c", "function": {"name": "b", "arguments": "{bad"}})
        assert call.arguments == {}
        assert call.malformed is True

    def test_empty_arguments_is_not_malformed(self):
        """A no-argument tool legitimately sends "" -- distinct from broken."""
        call = ToolCall.from_wire({"id": "c", "function": {"name": "b", "arguments": ""}})
        assert call.arguments == {} and call.malformed is False


class TestUsage:
    def test_accumulates(self):
        total = Usage(input_tokens=10, output_tokens=5) + Usage(input_tokens=3, output_tokens=2)
        assert (total.input_tokens, total.output_tokens) == (13, 7)

    def test_tracks_cache_tokens_separately(self):
        """Cache reads bill ~10x cheaper; folding them into input_tokens makes
        cost reporting wrong and hides whether caching works at all."""
        u = Usage(input_tokens=100, cache_read_tokens=900, cache_write_tokens=50)
        assert u.cache_read_tokens == 900 and u.cache_write_tokens == 50

    def test_total_tokens_counts_everything_sent(self):
        u = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=100, cache_write_tokens=2)
        assert u.total_tokens == 117


class TestStopReason:
    def test_covers_the_loop_exits(self):
        assert {s.value for s in StopReason} >= {"done", "max_turns", "error", "stopped"}


class TestErrors:
    def test_all_errors_share_one_base(self):
        """One `except GithiError` at the CLI boundary should catch everything
        this package raises deliberately."""
        assert issubclass(ToolNotFound, GithiError)

    def test_tool_not_found_names_the_tool(self):
        assert "nope" in str(ToolNotFound("nope"))


def test_message_is_the_shared_base():
    for m in (UserMessage("a"), SystemMessage("b"), AssistantMessage(content="c"),
              ToolResultMessage(tool_call_id="d", content="e")):
        assert isinstance(m, Message)
        assert isinstance(m.to_wire(), dict)


def test_unknown_message_type_cannot_be_constructed_silently():
    with pytest.raises(TypeError):
        UserMessage()  # content is required
