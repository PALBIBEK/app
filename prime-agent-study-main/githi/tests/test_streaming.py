"""Streaming completions (port of pi's `AssistantMessageEvent` flow).

The harness stopped needing this the moment it got a UI: without streaming a
human stares at a blank terminal for thirty seconds and then gets a wall of
text, which is indistinguishable from a hang.

Two things make this harder than "yield the chunks":

**Tool-call arguments arrive fragmented.** The provider sends `{"comm`,
`and": "git l`, `og"}` across separate chunks. Accumulated JSON is invalid at
every point until the last fragment lands, so parsing early yields garbage
and executing that garbage runs a command nobody asked for. Arguments are
therefore accumulated as *text* and parsed exactly once, at the end.

**The terminal event is the source of truth.** A consumer must be able to
ignore every delta and read only `done` to get the same message the blocking
client would have returned. That is what keeps the loop's logic identical
whether or not anyone is watching, and it is what these tests pin hardest.

SSE framing itself is unglamorous but unforgiving: multi-line frames, the
`[DONE]` sentinel, keep-alive comments, and chunks split mid-JSON.
"""

from __future__ import annotations

import json

import httpx
import pytest

from githi.llm.client import LLMClient
from githi.llm.streaming import StreamEvent, StreamEventType, accumulate


def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def _delta(**kw) -> dict:
    return {"choices": [{"index": 0, "delta": kw}]}


def _client(body: bytes) -> LLMClient:
    def handler(request):
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    return LLMClient(api_key="k", model="test-model",
                     http_client=httpx.Client(transport=transport))


def _events(body: bytes) -> list[StreamEvent]:
    return list(_client(body).stream([], []))


class TestTextStreaming:
    def test_text_deltas_are_emitted_in_order(self):
        events = _events(_sse(_delta(content="Hel"), _delta(content="lo")))
        deltas = [e.delta for e in events if e.type is StreamEventType.TEXT_DELTA]
        assert deltas == ["Hel", "lo"]

    def test_the_done_event_carries_the_assembled_message(self):
        events = _events(_sse(_delta(content="Hel"), _delta(content="lo")))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.content == "Hello"

    def test_a_consumer_may_ignore_every_delta(self):
        """The property that keeps the loop identical whether or not a human
        is watching: `done` alone is sufficient."""
        events = _events(_sse(_delta(content="a"), _delta(content="b")))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.content == "ab"

    def test_empty_content_deltas_are_not_emitted(self):
        """Providers send keep-alive deltas with no content; forwarding them
        makes a caller's 'has it started?' check useless."""
        events = _events(_sse(_delta(content=""), _delta(content="x")))
        assert len([e for e in events if e.type is StreamEventType.TEXT_DELTA]) == 1


class TestToolCallAssembly:
    def test_arguments_fragmented_across_chunks_are_reassembled(self):
        """The core difficulty. Accumulated JSON is invalid until the last
        fragment, so it must be parsed once at the end -- never per chunk."""
        events = _events(_sse(
            _delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "bash", "arguments": '{"comm'}}]),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": 'and": "git l'}}]),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": 'og"}'}}]),
        ))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        call = done.message.tool_calls[0]
        assert call.name == "bash" and call.arguments == {"command": "git log"}

    def test_the_id_survives_from_the_first_fragment(self):
        events = _events(_sse(
            _delta(tool_calls=[{"index": 0, "id": "c1",
                                "function": {"name": "bash", "arguments": "{}"}}]),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": ""}}]),
        ))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.tool_calls[0].id == "c1"

    def test_parallel_tool_calls_are_kept_apart_by_index(self):
        """The model batches calls; `index` is the only thing distinguishing
        two interleaved argument streams."""
        events = _events(_sse(
            _delta(tool_calls=[{"index": 0, "id": "c1",
                                "function": {"name": "read", "arguments": '{"p":"a"}'}}]),
            _delta(tool_calls=[{"index": 1, "id": "c2",
                                "function": {"name": "read", "arguments": '{"p":"b"}'}}]),
        ))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert [c.arguments["p"] for c in done.message.tool_calls] == ["a", "b"]

    def test_malformed_arguments_are_reported_not_executed(self):
        """Truncated JSON must surface as a malformed call, never as an empty
        argument dict -- `{}` would run the tool with defaults."""
        events = _events(_sse(
            _delta(tool_calls=[{"index": 0, "id": "c1",
                                "function": {"name": "bash", "arguments": '{"comm'}}]),
        ))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.tool_calls[0].malformed

    def test_a_toolcall_end_event_is_emitted_per_call(self):
        events = _events(_sse(
            _delta(tool_calls=[{"index": 0, "id": "c1",
                                "function": {"name": "bash", "arguments": "{}"}}]),
        ))
        assert any(e.type is StreamEventType.TOOLCALL_END for e in events)


class TestThinkingStreaming:
    """Pi's thinking_start / thinking_delta / thinking_end over OpenRouter fields."""

    def test_reasoning_content_deltas_are_emitted(self):
        events = _events(_sse(
            _delta(reasoning_content="hmm"),
            _delta(reasoning_content="..."),
            _delta(content="answer"),
        ))
        kinds = [e.type for e in events]
        assert StreamEventType.THINKING_START in kinds
        assert StreamEventType.THINKING_DELTA in kinds
        assert StreamEventType.THINKING_END in kinds
        thinking = "".join(e.delta for e in events
                           if e.type is StreamEventType.THINKING_DELTA)
        assert thinking == "hmm..."
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.thinking == "hmm..."
        assert done.message.thinking_signature == "reasoning_content"
        assert done.message.content == "answer"

    def test_only_the_first_nonempty_reasoning_field_counts(self):
        """Providers sometimes emit both `reasoning` and `reasoning_content`;
        taking both would double the text."""
        events = _events(_sse(_delta(reasoning_content="a", reasoning="b")))
        thinking = "".join(e.delta for e in events
                           if e.type is StreamEventType.THINKING_DELTA)
        assert thinking == "a"

    def test_reasoning_alias_is_accepted(self):
        events = _events(_sse(_delta(reasoning="plan")))
        done = [e for e in events if e.type is StreamEventType.DONE][-1]
        assert done.message.thinking == "plan"
        assert done.message.thinking_signature == "reasoning"


class TestSseFraming:
    def test_the_done_sentinel_ends_the_stream(self):
        events = _events(_sse(_delta(content="x")))
        assert events[-1].type is StreamEventType.DONE

    def test_keep_alive_comments_are_ignored(self):
        body = (b": keep-alive\n\n"
                + f"data: {json.dumps(_delta(content='x'))}\n\n".encode()
                + b"data: [DONE]\n\n")
        assert any(e.type is StreamEventType.TEXT_DELTA for e in _events(body))

    def test_a_non_json_data_line_does_not_kill_the_stream(self):
        """One malformed frame must not discard a response that is otherwise
        fine -- the text before and after it is still real."""
        body = (b"data: {not json}\n\n"
                + f"data: {json.dumps(_delta(content='x'))}\n\n".encode()
                + b"data: [DONE]\n\n")
        done = [e for e in _events(body) if e.type is StreamEventType.DONE][-1]
        assert done.message.content == "x"

    def test_finish_reason_is_captured(self):
        body = _sse(_delta(content="x"),
                    {"choices": [{"index": 0, "delta": {},
                                  "finish_reason": "tool_calls"}]})
        done = [e for e in _events(body) if e.type is StreamEventType.DONE][-1]
        assert done.finish_reason == "tool_calls"

    def test_usage_is_captured_when_the_provider_sends_it(self):
        body = _sse(_delta(content="x"),
                    {"choices": [], "usage": {"prompt_tokens": 10,
                                              "completion_tokens": 3}})
        done = [e for e in _events(body) if e.type is StreamEventType.DONE][-1]
        assert done.usage.input_tokens == 10 and done.usage.output_tokens == 3


class TestAccumulateHelper:
    """`accumulate` is the pure part: events in, final message out. Separated
    so the assembly logic is testable without any HTTP at all."""

    def test_it_returns_the_same_message_as_the_stream(self):
        events = _events(_sse(_delta(content="ab"),
                              _delta(tool_calls=[{"index": 0, "id": "c1",
                                                  "function": {"name": "bash",
                                                               "arguments": "{}"}}])))
        message = accumulate(e for e in events
                             if e.type is not StreamEventType.DONE)
        assert message.content == "ab" and message.tool_calls[0].name == "bash"

    def test_an_empty_stream_yields_an_empty_message(self):
        assert accumulate(iter(())).content == ""


class TestBlockingPathUnchanged:
    def test_complete_still_works(self):
        """832 tests depend on it; streaming is additive."""
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        client = LLMClient(api_key="k", model="m",
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert client.complete([], []).message.content == "hi"
