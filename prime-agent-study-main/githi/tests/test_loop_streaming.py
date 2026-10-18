"""The loop streams when the client can, and behaves identically either way.

The transport exists and the UI needs it, but the loop still calls
`complete()` -- so nothing a user sees would change. This is the wiring, and
the property worth protecting is that it changes *only* what is visible:
same transcript, same tool calls, same stop reason whether streaming or not.

Text deltas are re-emitted as agent events, so the renderer subscribes to the
agent rather than reaching into the LLM client. That keeps the TUI ignorant
of providers and lets a non-interactive caller ignore the events entirely.

A client without a `stream()` method must keep working. Every fake LLM in
this suite is such a client, and demanding the method would be a breaking
change to eight test files for no benefit.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.events import EventType
from githi.agent.interrupt import Interrupt
from githi.llm.client import Completion
from githi.llm.streaming import StreamEvent, StreamEventType
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, StopReason, ToolCall, Usage


class StreamingLLM:
    """Yields deltas, then a `done` event carrying the whole message."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.streamed = 0
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        raise AssertionError("the loop should have streamed")

    def stream(self, messages, tools, interrupt=None, **kw):
        self.calls.append(list(messages))
        self.streamed += 1
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        for piece in (message.content[i:i + 3]
                      for i in range(0, len(message.content), 3)):
            yield StreamEvent(StreamEventType.TEXT_DELTA, delta=piece)
        yield StreamEvent(StreamEventType.DONE, message=message,
                          usage=Usage(input_tokens=1),
                          finish_reason="tool_calls" if message.tool_calls else "stop")


class BlockingLLM:
    """No `stream` method at all -- like every other fake in this suite."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _tool():
    return Tool(name="bash", description="d", parameters={"type": "object"},
                run=lambda a, c: ToolResult(content="ran"))


def _agent(llm, tmp_path, **cfg):
    return Agent(llm=llm, tools=ToolRegistry([_tool()]),
                 policy=SandboxPolicy(root=tmp_path), config=AgentConfig(**cfg))


class TestStreamingIsUsed:
    def test_a_streaming_client_streams(self, tmp_path):
        llm = StreamingLLM([AssistantMessage(content="hello")])
        _agent(llm, tmp_path).run("go")
        assert llm.streamed == 1

    def test_deltas_are_re_emitted_as_agent_events(self, tmp_path):
        """The renderer subscribes to the agent, not the provider."""
        agent = _agent(StreamingLLM([AssistantMessage(content="hello there")]),
                       tmp_path)
        seen: list[str] = []
        agent.subscribe(lambda e: seen.append(e.data.get("delta", ""))
                        if e.type is EventType.TEXT_DELTA else None)
        agent.run("go")
        assert "".join(seen) == "hello there"

    def test_the_transcript_holds_the_assembled_message(self, tmp_path):
        agent = _agent(StreamingLLM([AssistantMessage(content="hello")]), tmp_path)
        agent.run("go")
        assert any(getattr(m, "content", "") == "hello"
                   for m in agent.state.messages)

    def test_tool_calls_still_execute(self, tmp_path):
        agent = _agent(StreamingLLM([
            AssistantMessage(content="", tool_calls=[ToolCall("c1", "bash", {})]),
            AssistantMessage(content="done")]), tmp_path)
        agent.run("go")
        assert agent.state.tool_calls == 1

    def test_usage_is_recorded(self, tmp_path):
        agent = _agent(StreamingLLM([AssistantMessage(content="hi")]), tmp_path)
        result = agent.run("go")
        assert result.usage.total.input_tokens == 1


class TestBlockingStillWorks:
    def test_a_client_without_stream_uses_complete(self, tmp_path):
        llm = BlockingLLM([AssistantMessage(content="hello")])
        assert _agent(llm, tmp_path).run("go").output == "hello"

    def test_the_result_is_the_same_either_way(self, tmp_path):
        """The property that makes this safe: streaming changes what is
        *visible*, never what happens."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall("c1", "bash", {})]),
                  AssistantMessage(content="final answer")]
        streamed = _agent(StreamingLLM(list(script)), tmp_path).run("go")
        blocking = _agent(BlockingLLM(list(script)), tmp_path).run("go")
        assert streamed.output == blocking.output
        assert streamed.stop_reason == blocking.stop_reason
        assert streamed.tool_calls == blocking.tool_calls


class TestInterruptDuringStreaming:
    def test_the_interrupt_reaches_the_client(self, tmp_path):
        """It has to be passed down, or ESC only takes effect between turns --
        which on a long answer is exactly when it feels broken."""
        seen = {}

        class Recording(StreamingLLM):
            def stream(self, messages, tools, interrupt=None, **kw):
                seen["interrupt"] = interrupt
                yield from super().stream(messages, tools, **kw)

        flag = Interrupt()
        _agent(Recording([AssistantMessage(content="hi")]), tmp_path,
               interrupt=flag).run("go")
        assert seen["interrupt"] is flag

    def test_a_partial_answer_is_kept(self, tmp_path):
        """Half an answer is information, and the user already paid for it."""
        class Interrupting:
            model = "m"

            def __init__(self):
                self.calls = []

            def stream(self, messages, tools, interrupt=None, **kw):
                self.calls.append(list(messages))
                yield StreamEvent(StreamEventType.TEXT_DELTA, delta="partial ")
                if interrupt:
                    interrupt.set()
                yield StreamEvent(StreamEventType.DONE,
                                  message=AssistantMessage(content="partial "),
                                  usage=Usage(), finish_reason="aborted")

        flag = Interrupt()
        agent = _agent(Interrupting(), tmp_path, interrupt=flag)
        result = agent.run("go")
        assert any("partial" in (getattr(m, "content", "") or "")
                   for m in agent.state.messages)
        assert result.stop_reason is StopReason.INTERRUPTED
