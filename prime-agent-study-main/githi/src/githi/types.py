"""The message and tool types every other module speaks.

Modelled on the OpenAI chat-completions wire format because that is what the
overwhelming majority of providers accept (directly or through a gateway like
OpenRouter). `to_wire()` is the single place that shape is produced, so a
provider quirk gets fixed once rather than at every call site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(str, Enum):
    DONE = "done"
    """The model answered without requesting another tool."""
    MAX_TURNS = "max_turns"
    """Budget exhausted mid-task -- NOT the same as done, and callers must be
    able to tell the difference before reporting a result as complete."""
    ERROR = "error"
    STOPPED = "stopped"
    """A hook or stop-condition ended the run deliberately."""
    INTERRUPTED = "interrupted"
    """The user pressed ESC. Distinct from STOPPED so the UI can say so, and
    distinct from DONE so nothing reports a half-finished run as an answer."""


@dataclass
class Usage:
    """Token accounting, with cache tokens kept separate.

    Cache reads bill at roughly a tenth of fresh input tokens, so collapsing
    them into `input_tokens` would both misreport cost and destroy the only
    evidence that caching is working at all.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    malformed: bool = False
    """Set when the provider returned unparseable JSON for `arguments`.

    Observed in practice on several gateway-routed models: the arguments
    string arrives truncated. Raising here would kill the whole run over one
    bad token; instead the call survives with empty arguments and this flag,
    and the tool pipeline turns it into an ordinary failed tool result the
    model can see and retry.
    """

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> ToolCall:
        fn = raw.get("function") or {}
        text = fn.get("arguments") or ""
        arguments: dict[str, Any] = {}
        malformed = False
        if text:
            try:
                parsed = json.loads(text)
                arguments = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (json.JSONDecodeError, TypeError):
                malformed = True
        return cls(id=raw.get("id", ""), name=fn.get("name", ""),
                   arguments=arguments, malformed=malformed)

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            # The schema requires a JSON *string* here, not an object. Sending
            # a dict is accepted by some gateways and rejected by others.
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass
class Message:
    """Base for everything in a transcript."""

    def to_wire(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass
class SystemMessage(Message):
    content: str

    def to_wire(self) -> dict[str, Any]:
        return {"role": Role.SYSTEM.value, "content": self.content}


@dataclass
class UserMessage(Message):
    content: str

    def to_wire(self) -> dict[str, Any]:
        return {"role": Role.USER.value, "content": self.content}


@dataclass
class AssistantMessage(Message):
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    """Extended reasoning text (pi's ThinkingContent), when the provider
    streams it separately from the visible answer."""
    thinking_signature: str = ""
    """Wire field the thinking arrived on (`reasoning_content` / `reasoning` /
    `reasoning_text`). Replay uses the same field so the provider sees what
    it originally emitted."""

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"role": Role.ASSISTANT.value, "content": self.content}
        # Omitted entirely when empty: `"tool_calls": []` is not equivalent to
        # absence, and some providers reject the empty array.
        if self.tool_calls:
            wire["tool_calls"] = [c.to_wire() for c in self.tool_calls]
        # Replay thinking on the field the provider used. Without this, a
        # multi-turn tool loop drops the reasoning the model already paid for
        # and some Anthropic-via-OR paths refuse the next turn.
        if self.thinking:
            field = self.thinking_signature or "reasoning_content"
            wire[field] = self.thinking
        return wire


@dataclass
class ToolResultMessage(Message):
    tool_call_id: str
    content: str
    is_error: bool = False
    """Carried for the harness's own telemetry. Deliberately not sent on the
    wire: the OpenAI tool-result schema has no error field, and the model
    learns something failed from the content itself."""

    def to_wire(self) -> dict[str, Any]:
        return {
            "role": Role.TOOL.value,
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


# ===========================================================================
# Agent-core contracts (port of pi's `types.ts`)
#
# Everything above is the LLM wire format — what pi imports from `pi-ai`.
# Everything below is the agent runtime's own vocabulary, which the first
# pass omitted entirely.
# ===========================================================================


class ToolExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    """Each call is prepared, executed and finalised before the next starts."""
    PARALLEL = "parallel"
    """Calls are prepared in order, then allowed tools run concurrently.
    Faster, but per-call attribution of side effects becomes ambiguous."""


class QueueMode(str, Enum):
    """How many queued user messages drain at once.

    `ONE_AT_A_TIME` exists because draining everything can hand the model
    five steering messages simultaneously, which reads as contradictory
    instructions rather than a sequence of corrections.
    """

    ALL = "all"
    ONE_AT_A_TIME = "one-at-a-time"


class ThinkingLevel(str, Enum):
    """Requested reasoning effort (pi's `ThinkingLevel`).

    A `str` enum so `.value` is pi's own vocabulary and `ThinkingLevel("high")`
    round-trips — which matters the moment a level is written to a session
    file or config and read back.

    Comparison operators are defined explicitly because `str` already
    provides them and they would compare *lexicographically*: `"high" <
    "low"` is True as text and false as meaning. Silently reversed ordering
    on a level check is exactly the kind of bug that looks like a model
    quirk for a week.
    """

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"

    @property
    def rank(self) -> int:
        return _THINKING_ORDER.index(self.value)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ThinkingLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, ThinkingLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, ThinkingLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, ThinkingLevel):
            return NotImplemented
        return self.rank >= other.rank


_THINKING_ORDER = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass
class TextContent:
    text: str
    type: str = "text"

    def to_wire(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class ImageContent:
    data: str
    """Base64-encoded bytes."""
    mime_type: str = "image/png"
    type: str = "image"

    def to_wire(self) -> dict[str, Any]:
        return {"type": "image",
                "source": {"type": "base64", "media_type": self.mime_type,
                           "data": self.data}}


ContentBlock = TextContent | ImageContent


def text_blocks(text: str) -> list[ContentBlock]:
    """The common case: one text block. Most tools return only text, and
    writing `[TextContent(...)]` at every call site obscures that."""
    return [TextContent(text)]


@dataclass
class AgentToolResult:
    """What a tool returns (pi's `AgentToolResult<T>`).

    `content` is a **list of blocks**, not a string, so a tool can return an
    image, and so model-visible text stays separate from `details` — which
    the harness reads for telemetry and never sends to the model.
    """

    content: list[ContentBlock] = field(default_factory=list)
    details: Any = None
    is_error: bool = False
    usage: Usage | None = None
    added_tool_names: list[str] = field(default_factory=list)
    """Tools introduced by this result, callable from this point onward.

    The substrate for runtime capability synthesis: a tool that writes a new
    tool has no way to make it callable without this."""
    terminate: bool = False
    """Hint to stop after the current batch. pi only honours it when *every*
    result in the batch sets it — one tool must not end a run the others are
    still working on."""

    def __post_init__(self) -> None:
        # pi types `content` as `string | (TextContent | ImageContent)[]`.
        # Accepting the string form and normalising here means the ~40 tools
        # and call sites that only ever return text do not each have to wrap
        # it, while the block form stays available for images.
        if isinstance(self.content, str):
            self.content = text_blocks(self.content)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextContent))


AgentMessage = Message
"""pi's `AgentMessage = Message | CustomAgentMessages[...]`.

In Python the union collapses because custom entries subclass `Message`. The
*distinction* is preserved where it matters — `harness.messages.convert_to_llm`,
which decides what actually reaches the provider. Custom entry types live in
`harness/messages.py`, mirroring pi's placement (they are harness concepts,
not wire-format ones).
"""


@dataclass
class AgentContext:
    """A snapshot of what one provider request sees (pi's `AgentContext`)."""

    system_prompt: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)


@dataclass
class BeforeToolCallResult:
    """Returned from `before_tool_call`. `block=True` prevents execution and
    the loop substitutes an error result carrying `reason`."""

    block: bool = False
    reason: str = ""
    arguments: dict[str, Any] | None = None


@dataclass
class AfterToolCallResult:
    """Partial override of an executed result (pi's `AfterToolCallResult`).

    Merge is **field-by-field, no deep merge**: a provided field replaces the
    original in full, an omitted field keeps it. Hence every field defaults
    to `None` — the type has to distinguish "not provided" from "provided as
    empty", and `[]` or `False` cannot.
    """

    content: list[ContentBlock] | None = None
    details: Any = None
    is_error: bool | None = None
    usage: Usage | None = None
    terminate: bool | None = None


@dataclass
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any] = field(default_factory=dict)
    context: AgentContext = field(default_factory=AgentContext)
    state: Any = None


@dataclass
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any] = field(default_factory=dict)
    result: AgentToolResult | None = None
    is_error: bool = False
    context: AgentContext = field(default_factory=AgentContext)
    state: Any = None


@dataclass
class ShouldStopAfterTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage] = field(default_factory=list)
    context: AgentContext = field(default_factory=AgentContext)
    new_messages: list[Message] = field(default_factory=list)
    state: Any = None


@dataclass
class PrepareNextTurnContext(ShouldStopAfterTurnContext):
    """Identical shape to `ShouldStopAfterTurnContext` (pi extends it), kept
    as its own name so a hook's signature says which decision it makes."""


@dataclass
class AgentLoopTurnUpdate:
    """Replacement runtime state for the next provider request.

    This is the primitive a phase machine is built from: returning a new
    `context` swaps the system prompt and tool set between turns, so phases
    need no framework of their own. Every field is optional and `None` means
    "keep what the loop already had".
    """

    context: AgentContext | None = None
    model: str | None = None
    thinking_level: ThinkingLevel | None = None
    tool_choice: str | None = None
