"""Assembling a streamed assistant message from provider deltas.

Kept apart from `client.py` because the assembly is pure -- events in, message
out -- and the HTTP is not. Everything genuinely tricky about streaming lives
here and can be tested without a socket.

**Tool-call arguments arrive fragmented.** A provider sends `{"comm`, then
`and": "git l`, then `og"}`. The accumulated text is invalid JSON at every
point but the last, so it is kept as *text* and parsed exactly once when the
stream ends. Parsing per chunk would either throw constantly or -- worse --
succeed on a prefix that happens to be valid and run a command nobody asked
for.

**`index`, not `id`, identifies a call.** Only the first fragment of each call
carries an id; subsequent fragments are anonymous and are matched by index.
Two batched tool calls interleave their fragments, so index is the only thing
keeping them apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator

from ..types import AssistantMessage, ToolCall, Usage


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    TOOLCALL_START = "toolcall_start"
    TOOLCALL_DELTA = "toolcall_delta"
    TOOLCALL_END = "toolcall_end"
    DONE = "done"
    ERROR = "error"


# OpenRouter / openai-compat providers disagree on the field name. Take the
# first non-empty (pi's openai-completions path) so dual-emitting gateways
# do not double-count the same reasoning tokens.
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")


@dataclass
class StreamEvent:
    type: StreamEventType
    delta: str = ""
    """The new text for this event only -- never the accumulated total. A
    renderer appends it directly; handing it the running total would make it
    re-print everything on every chunk."""

    index: int = 0
    tool_call: ToolCall | None = None
    message: AssistantMessage | None = None
    """Set on DONE: the complete message, identical to what the blocking
    client would have returned. A consumer may ignore every delta and read
    only this."""

    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    error: str = ""


@dataclass
class _PartialCall:
    id: str = ""
    name: str = ""
    arguments: str = ""
    """Raw text, deliberately. See the module docstring."""

    def finish(self) -> ToolCall:
        text = self.arguments.strip() or "{}"
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be an object")
            return ToolCall(id=self.id, name=self.name, arguments=parsed)
        except (json.JSONDecodeError, ValueError):
            # Flagged, never silently emptied. `{}` would look like a valid
            # call with no arguments and run the tool with its defaults --
            # which is how a truncated `rm -rf /tmp/x` becomes `rm -rf /`.
            return ToolCall(id=self.id, name=self.name, arguments={},
                            malformed=True)


class MessageAssembler:
    """Feed it provider deltas; ask it for events and a final message."""

    def __init__(self) -> None:
        self.content = ""
        self.thinking = ""
        self.thinking_signature = ""
        """Which wire field carried the thinking (`reasoning_content` etc.).
        Stored so replay can put it back on the same field the provider used."""
        self._thinking_open = False
        self.calls: dict[int, _PartialCall] = {}
        self.usage = Usage()
        self.finish_reason = "stop"

    def feed(self, chunk: dict) -> Iterator[StreamEvent]:
        if usage := chunk.get("usage"):
            from .usage import parse_usage

            self.usage = parse_usage(usage)

        for choice in chunk.get("choices") or []:
            if reason := choice.get("finish_reason"):
                self.finish_reason = reason

            delta = choice.get("delta") or {}

            yield from self._feed_thinking(delta)

            text = delta.get("content")
            if text:
                # Empty deltas are dropped rather than forwarded: providers
                # send them as keep-alives, and a consumer checking "has it
                # started?" would be told yes before any text existed.
                # Closing an open thinking block first matches pi: text that
                # follows reasoning ends the thinking phase.
                yield from self._close_thinking()
                self.content += text
                yield StreamEvent(StreamEventType.TEXT_DELTA, delta=text)

            for raw in delta.get("tool_calls") or []:
                yield from self._close_thinking()
                yield from self._feed_tool_call(raw)

    def _feed_thinking(self, delta: dict) -> Iterator[StreamEvent]:
        found = None
        for field in _REASONING_FIELDS:
            value = delta.get(field)
            if isinstance(value, str) and value:
                found = field
                break
        if found is None:
            return
        if not self._thinking_open:
            self.thinking_signature = found
            self._thinking_open = True
            yield StreamEvent(StreamEventType.THINKING_START)
        self.thinking += delta[found]
        yield StreamEvent(StreamEventType.THINKING_DELTA, delta=delta[found])

    def _close_thinking(self) -> Iterator[StreamEvent]:
        if not self._thinking_open:
            return
        self._thinking_open = False
        yield StreamEvent(StreamEventType.THINKING_END, delta=self.thinking)

    def _feed_tool_call(self, raw: dict) -> Iterator[StreamEvent]:
        index = raw.get("index", 0)
        is_new = index not in self.calls
        call = self.calls.setdefault(index, _PartialCall())

        if is_new:
            yield StreamEvent(StreamEventType.TOOLCALL_START, index=index)

        # Only the first fragment carries these; later ones must not clear them.
        if raw.get("id"):
            call.id = raw["id"]

        function = raw.get("function") or {}
        if function.get("name"):
            call.name = function["name"]

        fragment = function.get("arguments")
        if fragment:
            call.arguments += fragment
            yield StreamEvent(StreamEventType.TOOLCALL_DELTA, delta=fragment,
                              index=index)

    def finish(self) -> Iterator[StreamEvent]:
        """Terminal events: one `toolcall_end` per call, then `done`."""
        yield from self._close_thinking()
        tool_calls = []
        for index in sorted(self.calls):
            call = self.calls[index].finish()
            tool_calls.append(call)
            yield StreamEvent(StreamEventType.TOOLCALL_END, index=index,
                              tool_call=call)

        yield StreamEvent(
            StreamEventType.DONE,
            message=AssistantMessage(
                content=self.content, tool_calls=tool_calls,
                thinking=self.thinking,
                thinking_signature=self.thinking_signature),
            usage=self.usage, finish_reason=self.finish_reason)


def accumulate(events: Iterable[StreamEvent]) -> AssistantMessage:
    """Rebuild the message from events alone.

    Useful to a consumer that buffered the stream and no longer has the
    assembler -- and as the independent check that the deltas really do carry
    everything the final message contains.
    """
    content = ""
    thinking = ""
    calls: list[ToolCall] = []
    for event in events:
        if event.type is StreamEventType.TEXT_DELTA:
            content += event.delta
        elif event.type is StreamEventType.THINKING_DELTA:
            thinking += event.delta
        elif event.type is StreamEventType.TOOLCALL_END and event.tool_call:
            calls.append(event.tool_call)
        elif event.type is StreamEventType.DONE and event.message:
            return event.message
    return AssistantMessage(content=content, tool_calls=calls, thinking=thinking)
