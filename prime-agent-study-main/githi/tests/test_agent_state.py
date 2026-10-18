"""AgentState and events."""

from __future__ import annotations

from githi.agent.events import Event, EventType
from githi.agent.state import AgentState
from githi.types import AssistantMessage, StopReason, ToolCall, Usage, UserMessage


class TestState:
    def test_starts_empty(self):
        s = AgentState()
        assert s.messages == [] and s.turns == 0 and s.stop_reason is None

    def test_append_message(self):
        s = AgentState()
        s.append(UserMessage("hi"))
        assert s.messages == [UserMessage("hi")]

    def test_records_usage_across_turns(self):
        s = AgentState()
        s.record_turn(Usage(input_tokens=10, output_tokens=2))
        s.record_turn(Usage(input_tokens=5))
        assert s.turns == 2 and s.usage.total.input_tokens == 15

    def test_tool_call_counter(self):
        """Tool calls and turns diverge whenever the model batches several
        calls into one message -- and that gap is exactly what shows batching
        is happening."""
        s = AgentState()
        s.record_tool_call("bash")
        s.record_tool_call("bash")
        s.record_tool_call("read")
        assert s.tool_calls == 3
        assert s.tool_counts == {"bash": 2, "read": 1}

    def test_stop_reason_is_settable(self):
        s = AgentState()
        s.stop_reason = StopReason.MAX_TURNS
        assert s.stop_reason is StopReason.MAX_TURNS

    def test_last_assistant_message(self):
        s = AgentState()
        s.append(UserMessage("q"))
        s.append(AssistantMessage(content="answer"))
        assert s.last_assistant_text() == "answer"

    def test_last_assistant_text_when_none(self):
        assert AgentState().last_assistant_text() == ""

    def test_ignores_tool_only_assistant_messages_when_reporting(self):
        """A message that only requests tools is not the final answer."""
        s = AgentState()
        s.append(AssistantMessage(content="real answer"))
        s.append(AssistantMessage(content="",
                                  tool_calls=[ToolCall(id="c", name="bash")]))
        assert s.last_assistant_text() == "real answer"


class TestEvents:
    def test_listeners_receive_events(self):
        seen = []
        s = AgentState()
        s.on(seen.append)
        s.emit(Event(EventType.TURN_START, {"turn": 1}))
        assert seen[0].type is EventType.TURN_START

    def test_multiple_listeners(self):
        a, b = [], []
        s = AgentState()
        s.on(a.append)
        s.on(b.append)
        s.emit(Event(EventType.TURN_END, {}))
        assert len(a) == 1 and len(b) == 1

    def test_listener_exception_does_not_break_the_run(self):
        """Observability is not worth failing a task over."""
        s = AgentState()
        s.on(lambda e: (_ for _ in ()).throw(RuntimeError("bad listener")))
        seen = []
        s.on(seen.append)
        s.emit(Event(EventType.TURN_START, {}))
        assert len(seen) == 1

    def test_event_types_cover_the_lifecycle(self):
        assert {e.value for e in EventType} >= {
            "turn_start", "turn_end", "tool_start", "tool_end", "agent_end",
        }
