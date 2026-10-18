"""
Message handling for Githi runtime.

Message building, formatting, and transformation utilities:
- Build context from history
- Format tool results
- Add system prompts
- Message validation
- Role normalization
- Custom message types for transcript entries
- convert_to_llm for transcript -> LLM context conversion

Ported from githi/src/githi/harness/messages.py with additions.

Pattern: Utility functions with immutable data (PYTHON_PATTERNS_MASTERCLASS.md)
"""

from __future__ import annotations

import time
import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone

if t.TYPE_CHECKING:
    from supervisor.agent.base import Message, ToolCall, ToolResult


# === Custom Message Types (from harness/messages.py) ===

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the "
    "following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back "
    "from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


@dataclass
class CustomMessage:
    """An app-defined transcript entry.

    Reaches the model as a user message **by default**. `display` is a UI concern only;
    conflating it with model visibility would hide content from the model
    that the author meant to hide only from a terminal.
    """

    custom_type: str = ""
    content: str = ""
    display: bool = True
    details: t.Any = None
    timestamp: float = field(default_factory=time.time)

    @property
    def role(self) -> str:
        return "user"

    def to_llm(self) -> dict[str, t.Any] | None:
        """Convert to LLM message format."""
        # `details` is deliberately not sent: it is structured data for logs
        # and UI, and dumping it into the prompt is how internal state leaks
        # into a conversation.
        return {"role": "user", "content": self.content}


@dataclass
class BashExecutionMessage(CustomMessage):
    """A shell command the *user* ran (pi's `!command`), not a tool call."""

    command: str = ""
    output: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None
    exclude_from_context: bool = False
    """pi's `!!command`: run it, show the human, spend no context on it.
    The only opt-out in the whole conversion."""

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "bashExecution"

    def to_llm(self) -> dict[str, t.Any] | None:
        if self.exclude_from_context:
            return None
        return {"role": "user", "content": bash_execution_to_text(self)}


@dataclass
class BranchSummaryMessage(CustomMessage):
    """Summary from an abandoned branch carried forward."""

    summary: str = ""
    from_id: str = ""

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "branchSummary"

    def to_llm(self) -> dict[str, t.Any] | None:
        return {
            "role": "user",
            "content": BRANCH_SUMMARY_PREFIX + self.summary + BRANCH_SUMMARY_SUFFIX,
        }


@dataclass
class CompactionSummaryMessage(CustomMessage):
    """Summary from context compaction."""

    summary: str = ""
    tokens_before: int = 0

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "compactionSummary"

    def to_llm(self) -> dict[str, t.Any] | None:
        return {
            "role": "user",
            "content": COMPACTION_SUMMARY_PREFIX + self.summary + COMPACTION_SUMMARY_SUFFIX,
        }


def bash_execution_to_text(message: BashExecutionMessage) -> str:
    """Render a shell execution for the model.

    Fenced so the model can tell output from narration, and explicit about
    "(no output)" — a blank rendering reads as a failed command and invites
    a pointless re-run.
    """
    text = f"Ran `{message.command}`\n"
    text += f"```\n{message.output}\n```" if message.output else "(no output)"

    if message.cancelled:
        text += "\n\n(command cancelled)"
    elif message.exit_code not in (None, 0):
        text += f"\n\nCommand exited with code {message.exit_code}"

    if message.truncated and message.full_output_path:
        text += f"\n\n[Output truncated. Full output: {message.full_output_path}]"
    return text


def create_custom_message(
    custom_type: str, content: str, display: bool = True, details: t.Any = None
) -> CustomMessage:
    """Factory for CustomMessage."""
    return CustomMessage(
        custom_type=custom_type, content=content, display=display, details=details
    )


def create_branch_summary(summary: str, from_id: str) -> BranchSummaryMessage:
    """Factory for BranchSummaryMessage."""
    return BranchSummaryMessage(summary=summary, from_id=from_id)


def create_compaction_summary(
    summary: str, tokens_before: int
) -> CompactionSummaryMessage:
    """Factory for CompactionSummaryMessage."""
    return CompactionSummaryMessage(summary=summary, tokens_before=tokens_before)


def convert_to_llm(messages: list[t.Any]) -> list[dict[str, t.Any]]:
    """Transcript → what the provider receives.

    Ordinary messages pass through untouched; custom entries delegate to
    their own `to_llm()`, and a `None` means "not part of the context".
    Returns a new list — the caller's transcript is never consumed.
    """
    out: list[dict[str, t.Any]] = []
    for message in messages:
        if isinstance(message, CustomMessage):
            converted = message.to_llm()
            if converted is not None:
                out.append(converted)
            continue
        # Handle dict messages
        if isinstance(message, dict):
            out.append(message)
            continue
        # Handle objects with to_wire or to_dict methods
        if hasattr(message, "to_wire"):
            out.append(message.to_wire())
        elif hasattr(message, "to_dict"):
            out.append(message.to_dict())
        else:
            # Assume it's a Message-like object
            msg_dict = {"role": getattr(message, "role", "user")}
            if hasattr(message, "content"):
                msg_dict["content"] = message.content
            if hasattr(message, "tool_calls") and message.tool_calls:
                msg_dict["tool_calls"] = message.tool_calls
            if hasattr(message, "tool_call_id") and message.tool_call_id:
                msg_dict["tool_call_id"] = message.tool_call_id
            out.append(msg_dict)
    return out


@dataclass(frozen=True)
class MessageMetadata:
    """
    Metadata for message tracking.

    Immutable metadata attached to messages for debugging and analytics.
    """

    message_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str | None = None
    turn_number: int | None = None
    token_count: int | None = None  # Estimated token count


class MessageBuilder:
    """
    Builder for constructing messages.

    Provides fluent interface for building messages with validation.

    Example:
        msg = (
            MessageBuilder()
            .role("user")
            .content("Hello, world!")
            .session_id("sess-123")
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize message builder"""
        self._role: str | None = None
        self._content: str | None = None
        self._tool_calls: list[ToolCall] | None = None
        self._tool_call_id: str | None = None
        self._metadata: MessageMetadata | None = None

    def role(self, role: str) -> MessageBuilder:
        """Set message role"""
        self._role = role
        return self

    def content(self, content: str) -> MessageBuilder:
        """Set message content"""
        self._content = content
        return self

    def tool_calls(self, tool_calls: list[ToolCall]) -> MessageBuilder:
        """Set tool calls (for assistant messages)"""
        self._tool_calls = tool_calls
        return self

    def tool_call_id(self, tool_call_id: str) -> MessageBuilder:
        """Set tool call ID (for tool_result messages)"""
        self._tool_call_id = tool_call_id
        return self

    def metadata(self, metadata: MessageMetadata) -> MessageBuilder:
        """Set message metadata"""
        self._metadata = metadata
        return self

    def session_id(self, session_id: str) -> MessageBuilder:
        """Set session ID in metadata"""
        if not self._metadata:
            import uuid

            self._metadata = MessageMetadata(
                message_id=f"msg-{uuid.uuid4().hex[:8]}",
                session_id=session_id,
            )
        return self

    def build(self) -> Message:
        """Build message (validates required fields)"""
        if not self._role:
            raise ValueError("Message role required")

        if not self._content and not self._tool_calls:
            raise ValueError("Message must have content or tool_calls")

        return Message(
            role=self._role,
            content=self._content or "",
            tool_calls=self._tool_calls,
            tool_call_id=self._tool_call_id,
            metadata=self._metadata,
        )


class MessageFormatter:
    """
    Format messages for LLM API.

    Transforms internal message representation to LLM API format.

    Pattern: Stateless utility class (PYTHON_PATTERNS_MASTERCLASS.md)
    """

    @staticmethod
    def to_api_format(message: Message) -> dict[str, t.Any]:
        """
        Convert message to LLM API format.

        Args:
            message: Internal message

        Returns:
            Dict suitable for LLM API (OpenAI-compatible format)
        """
        result: dict[str, t.Any] = {
            "role": message.role,
            "content": message.content,
        }

        # Add tool calls if present (assistant messages)
        if hasattr(message, "tool_calls") and message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for call in message.tool_calls
            ]

        # Add tool call ID if present (tool_result messages)
        if hasattr(message, "tool_call_id") and message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id

        return result

    @staticmethod
    def to_display_format(message: Message) -> str:
        """
        Format message for display/logging.

        Args:
            message: Message to format

        Returns:
            Human-readable string
        """
        role = message.role.upper()
        content = message.content

        # Truncate long content
        if len(content) > 200:
            content = content[:200] + "..."

        # Add tool call info
        tool_info = ""
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_names = [call.name for call in message.tool_calls]
            tool_info = f" [tools: {', '.join(tool_names)}]"

        return f"[{role}]{tool_info} {content}"


class ContextBuilder:
    """
    Build context for LLM calls.

    Manages message history and adds system prompts, memory, etc.

    Pattern: Builder with incremental construction
    """

    def __init__(self, system_prompt: str | None = None) -> None:
        """
        Initialize context builder.

        Args:
            system_prompt: System prompt to prepend (optional)
        """
        self.system_prompt = system_prompt
        self.messages: list[Message] = []

    def add_system_prompt(self, prompt: str) -> ContextBuilder:
        """Add/update system prompt"""
        self.system_prompt = prompt
        return self

    def add_message(self, message: Message) -> ContextBuilder:
        """Add message to context"""
        self.messages.append(message)
        return self

    def add_messages(self, messages: list[Message]) -> ContextBuilder:
        """Add multiple messages to context"""
        self.messages.extend(messages)
        return self

    def add_memory_block(self, content: str, *, source: str = "memory") -> ContextBuilder:
        """
        Add memory block to context (Phase 3).

        Args:
            content: Memory content
            source: Memory source ("strategy" | "curated" | "session")
        """
        # Phase 1: Add as system message
        # Phase 3: Use dedicated memory injection
        memory_msg = (
            MessageBuilder()
            .role("system")
            .content(f"[Memory from {source}]\n{content}")
            .build()
        )
        self.messages.insert(0, memory_msg)  # Insert at start
        return self

    def build(self) -> list[Message]:
        """
        Build final context.

        Returns:
            List of messages for LLM API
        """
        context = []

        # Add system prompt first (if present)
        if self.system_prompt:
            system_msg = (
                MessageBuilder().role("system").content(self.system_prompt).build()
            )
            context.append(system_msg)

        # Add all messages
        context.extend(self.messages)

        return context

    def estimate_tokens(self) -> int:
        """
        Estimate token count for context.

        Phase 1: Rough estimate (4 chars = 1 token)
        Phase 2: Use tiktoken for accurate counting

        Returns:
            Estimated token count
        """
        total_chars = 0

        if self.system_prompt:
            total_chars += len(self.system_prompt)

        for msg in self.messages:
            total_chars += len(msg.content)

        # Rough estimate: 4 characters per token
        return total_chars // 4


class ToolResultFormatter:
    """
    Format tool results for messages.

    Converts tool execution results to message content.

    Pattern: Stateless utility class
    """

    @staticmethod
    def format_success(result: ToolResult) -> str:
        """
        Format successful tool result.

        Args:
            result: Tool result

        Returns:
            Formatted content string
        """
        import json

        output = result.output

        # If output is dict/list, format as JSON
        if isinstance(output, (dict, list)):
            try:
                return json.dumps(output, indent=2)
            except (TypeError, ValueError):
                return str(output)

        return str(output)

    @staticmethod
    def format_error(result: ToolResult) -> str:
        """
        Format failed tool result.

        Args:
            result: Tool result with error

        Returns:
            Formatted error message
        """
        return f"ERROR: {result.error}"

    @staticmethod
    def to_message(result: ToolResult) -> Message:
        """
        Convert tool result to message.

        Args:
            result: Tool result

        Returns:
            Message object (role=tool_result)
        """
        if result.success:
            content = ToolResultFormatter.format_success(result)
        else:
            content = ToolResultFormatter.format_error(result)

        return (
            MessageBuilder()
            .role("tool_result")
            .content(content)
            .tool_call_id(result.tool_call_id)
            .build()
        )


class MessageValidator:
    """
    Validate message structure.

    Ensures messages conform to expected format.

    Pattern: Stateless validator
    """

    VALID_ROLES = {"system", "user", "assistant", "tool_result"}

    @staticmethod
    def validate(message: Message) -> tuple[bool, str | None]:
        """
        Validate message.

        Args:
            message: Message to validate

        Returns:
            Tuple of (valid, error_message)
        """
        # Check role
        if message.role not in MessageValidator.VALID_ROLES:
            return False, f"Invalid role: {message.role}"

        # Check content or tool_calls present
        has_content = bool(message.content)
        has_tool_calls = bool(getattr(message, "tool_calls", None))

        if not has_content and not has_tool_calls:
            return False, "Message must have content or tool_calls"

        # tool_result must have tool_call_id
        if message.role == "tool_result":
            if not getattr(message, "tool_call_id", None):
                return False, "tool_result message must have tool_call_id"

        # assistant with tool_calls must have tool_calls list
        if message.role == "assistant" and has_tool_calls:
            tool_calls = getattr(message, "tool_calls", [])
            if not isinstance(tool_calls, list):
                return False, "tool_calls must be list"

        return True, None

    @staticmethod
    def validate_batch(messages: list[Message]) -> tuple[bool, str | None]:
        """
        Validate batch of messages.

        Args:
            messages: Messages to validate

        Returns:
            Tuple of (valid, error_message)
        """
        for i, msg in enumerate(messages):
            valid, error = MessageValidator.validate(msg)
            if not valid:
                return False, f"Message {i}: {error}"

        return True, None


# === Type Stubs for Protocol Compliance ===

if not t.TYPE_CHECKING:

    class Message:
        """Message object"""

        def __init__(
            self,
            role: str,
            content: str,
            *,
            tool_calls: list | None = None,
            tool_call_id: str | None = None,
            metadata: MessageMetadata | None = None,
        ):
            self.role = role
            self.content = content
            self.tool_calls = tool_calls
            self.tool_call_id = tool_call_id
            self.metadata = metadata

    class ToolCall:
        """Tool call"""

        def __init__(self, id: str, name: str, arguments: dict):
            self.id = id
            self.name = name
            self.arguments = arguments

    class ToolResult:
        """Tool result"""

        def __init__(
            self, tool_call_id: str, output: t.Any, success: bool, error: str | None = None
        ):
            self.tool_call_id = tool_call_id
            self.output = output
            self.success = success
            self.error = error
