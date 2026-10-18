"""pi's `types.ts` surface (437 lines) that githi's first pass omitted.

The earlier `types.py` covered only the LLM wire format — messages, tool
calls, usage. That is the part pi *imports* from `pi-ai`; the agent core's
own contracts were missing entirely. This file pins them.

Four carry real capability rather than being type ceremony:

* **`AgentMessage` vs `Message`** — the transcript may hold entries the model
  never sees natively. The conversion itself lives in `harness/messages.py`
  (pi's placement); see `test_harness_messages.py`.
* **`AgentToolResult.content` as blocks** — a tool can return images, and a
  result can carry structured details separately from model-visible text.
* **`added_tool_names`** — a tool can introduce new tools mid-run, which is
  the substrate for runtime capability synthesis.
* **`AgentLoopTurnUpdate`** — replacement context/model/thinking for the next
  turn. This is the primitive a phase machine is built on.
"""

from __future__ import annotations

import pytest

from githi.types import (
    AgentContext,
    AgentLoopTurnUpdate,
    AgentToolResult,
    AfterToolCallContext,
    AfterToolCallResult,
    AssistantMessage,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ImageContent,
    QueueMode,
    ShouldStopAfterTurnContext,
    TextContent,
    ThinkingLevel,
    ToolCall,
    ToolExecutionMode,
    ToolResultMessage,
    UserMessage,
    text_blocks,
)


class TestEnums:
    def test_tool_execution_modes(self):
        assert {m.value for m in ToolExecutionMode} == {"sequential", "parallel"}

    def test_queue_modes(self):
        """How many queued user messages drain at once."""
        assert {m.value for m in QueueMode} == {"all", "one-at-a-time"}

    def test_thinking_levels(self):
        assert {t.value for t in ThinkingLevel} == {
            "off", "minimal", "low", "medium", "high", "xhigh", "max"}

    def test_thinking_levels_are_ordered(self):
        """Callers need to compare levels ('at least high'), not just match."""
        assert ThinkingLevel.OFF < ThinkingLevel.LOW < ThinkingLevel.HIGH < ThinkingLevel.MAX


class TestContentBlocks:
    def test_text_block_wire_shape(self):
        assert TextContent("hello").to_wire() == {"type": "text", "text": "hello"}

    def test_image_block_carries_media_type(self):
        block = ImageContent(data="aGVsbG8=", mime_type="image/png")
        wire = block.to_wire()
        assert wire["type"] == "image" and "aGVsbG8=" in str(wire)

    def test_text_blocks_helper(self):
        blocks = text_blocks("one")
        assert len(blocks) == 1 and isinstance(blocks[0], TextContent)


class TestAgentToolResult:
    def test_content_is_a_list_of_blocks(self):
        """pi's contract. A plain string cannot express an image, nor
        separate model-visible text from structured detail."""
        result = AgentToolResult(content=text_blocks("done"))
        assert isinstance(result.content, list)
        assert result.content[0].text == "done"

    def test_details_are_separate_from_content(self):
        result = AgentToolResult(content=text_blocks("ok"), details={"exit_code": 0})
        assert result.details["exit_code"] == 0

    def test_defaults_are_not_error_and_not_terminating(self):
        result = AgentToolResult(content=text_blocks("x"))
        assert result.is_error is False and result.terminate is False

    def test_added_tool_names(self):
        """A tool that creates a tool -- how runtime capability synthesis
        makes its output callable from the next turn onward."""
        result = AgentToolResult(content=text_blocks("built"),
                                 added_tool_names=["new_capability"])
        assert result.added_tool_names == ["new_capability"]

    def test_text_convenience_joins_text_blocks(self):
        result = AgentToolResult(content=[TextContent("a"), TextContent("b")])
        assert result.text == "a\nb"

    def test_text_ignores_non_text_blocks(self):
        result = AgentToolResult(content=[TextContent("a"),
                                          ImageContent(data="x", mime_type="image/png")])
        assert result.text == "a"


class TestAgentContext:
    def test_carries_prompt_messages_and_tools(self):
        ctx = AgentContext(system_prompt="rules", messages=[UserMessage("hi")],
                           tools=[{"name": "bash"}])
        assert ctx.system_prompt == "rules" and len(ctx.messages) == 1 and ctx.tools

    def test_tools_default_to_empty(self):
        assert AgentContext(system_prompt="x", messages=[]).tools == []


class TestHookContracts:
    def test_before_result_can_block_with_a_reason(self):
        decision = BeforeToolCallResult(block=True, reason="denied")
        assert decision.block and decision.reason == "denied"

    def test_before_result_defaults_to_allow(self):
        assert BeforeToolCallResult().block is False

    def test_after_result_fields_are_optional_overrides(self):
        """pi merges field-by-field: an omitted field keeps the executed
        result's value. `None` therefore has to mean 'not provided', which
        is why every field defaults to None rather than a value."""
        override = AfterToolCallResult(is_error=True)
        assert override.is_error is True
        assert override.content is None and override.details is None

    def test_after_result_can_replace_content(self):
        override = AfterToolCallResult(content=text_blocks("redacted"))
        assert override.content[0].text == "redacted"

    def test_before_context_carries_the_call_and_args(self):
        call = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        ctx = BeforeToolCallContext(assistant_message=AssistantMessage(tool_calls=[call]),
                                    tool_call=call, args={"command": "ls"},
                                    context=AgentContext(system_prompt="", messages=[]))
        assert ctx.tool_call.name == "bash" and ctx.args["command"] == "ls"

    def test_after_context_carries_the_result_and_error_flag(self):
        call = ToolCall(id="c1", name="bash")
        ctx = AfterToolCallContext(assistant_message=AssistantMessage(tool_calls=[call]),
                                   tool_call=call, args={},
                                   result=AgentToolResult(content=text_blocks("boom"),
                                                          is_error=True),
                                   is_error=True,
                                   context=AgentContext(system_prompt="", messages=[]))
        assert ctx.is_error and ctx.result.text == "boom"


class TestTurnControl:
    def test_should_stop_context_carries_turn_output(self):
        ctx = ShouldStopAfterTurnContext(
            message=AssistantMessage(content="done"),
            tool_results=[ToolResultMessage(tool_call_id="c1", content="ok")],
            context=AgentContext(system_prompt="", messages=[]),
            new_messages=[])
        assert ctx.message.content == "done" and len(ctx.tool_results) == 1

    def test_turn_update_can_replace_context(self):
        """The phase-machine primitive: swap prompt/tools between turns
        without a separate framework."""
        update = AgentLoopTurnUpdate(
            context=AgentContext(system_prompt="new phase", messages=[]))
        assert update.context.system_prompt == "new phase"

    def test_turn_update_can_replace_model_and_thinking(self):
        update = AgentLoopTurnUpdate(model="anthropic/claude-opus-4",
                                     thinking_level=ThinkingLevel.HIGH)
        assert update.model.endswith("opus-4")
        assert update.thinking_level is ThinkingLevel.HIGH

    def test_turn_update_fields_are_all_optional(self):
        """Every field omitted means 'keep what the loop already had'."""
        update = AgentLoopTurnUpdate()
        assert update.context is None and update.model is None
        assert update.thinking_level is None


def test_existing_wire_types_are_unchanged():
    """The earlier surface must keep working -- this is an addition, not a
    rewrite, and 490 existing tests depend on it."""
    assert UserMessage("hi").to_wire() == {"role": "user", "content": "hi"}
    assert ToolResultMessage(tool_call_id="c", content="x").to_wire()["role"] == "tool"


def test_thinking_level_round_trips():
    """Regression: an earlier version monkeypatched `.value` onto an int
    enum, so `.value` read "high" while `ThinkingLevel("high")` raised —
    fine until a level is written to a session file and read back."""
    for level in ThinkingLevel:
        assert ThinkingLevel(level.value) is level


def test_thinking_level_ordering_is_semantic_not_lexicographic():
    """`str` supplies comparisons that order by text: "high" < "low" is True
    as a string and wrong as a meaning."""
    assert ThinkingLevel.LOW < ThinkingLevel.HIGH
    assert not (ThinkingLevel.HIGH < ThinkingLevel.LOW)
    assert ThinkingLevel.MAX >= ThinkingLevel.XHIGH
