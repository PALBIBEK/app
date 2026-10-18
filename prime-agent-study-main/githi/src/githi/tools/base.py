"""The tool contract.

A plain dataclass holding a callable, not a class hierarchy: a tool is
data (name, description, JSON schema) plus one function. Subclassing buys
nothing here and makes tools harder to build at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..sandbox.policy import SandboxPolicy
from ..types import AgentToolResult, ContentBlock, text_blocks


@dataclass
class ToolContext:
    """Everything a tool is allowed to know about the run.

    Deliberately narrow. A tool that could reach the agent, the transcript or
    the LLM client would let capability creep in through the back door; the
    policy plus a scratch dict is all any of pi's built-ins actually need.
    """

    policy: SandboxPolicy
    env: Any = None
    """How the tool reaches the filesystem and the shell.

    Defaults to a `LocalExecutionEnv` rooted at the sandbox, so existing
    callers that pass only a policy keep working. Substituting a Docker or
    SSH backend here is what relocates every tool at once -- which is only
    true for as long as no tool reaches for `pathlib` directly.

    It answers *how* to reach a path, never *whether* to: confinement stays
    in `sandbox.paths`, so swapping the backend cannot drop those checks.
    """

    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.env is None:
            from ..harness.env import LocalExecutionEnv

            self.env = LocalExecutionEnv(self.policy.root)


# `ToolResult` is pi's `AgentToolResult`: content is a **list of blocks**, not
# a string, so a tool can return an image alongside text and keep structured
# `details` out of the model's view. Aliased rather than redefined so there is
# one definition of the shape.
ToolResult = AgentToolResult


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], ToolResult]
    output_schema: dict[str, Any] | None = None
    """Optional JSON Schema for successful tool output (DeepSeek-style).

    Not sent on the OpenAI tools wire today; kept on the Tool so harness code
    and tests can enforce/advertise the contract the model text must match."""

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


def ok(content: str, **details: Any) -> ToolResult:
    """Success with a single text block -- the shape almost every tool wants.

    Tools returning images build `AgentToolResult` directly; this keeps the
    common case from writing `[TextContent(...)]` at every call site.
    """
    return ToolResult(content=text_blocks(content), details=details)


def fail(content: str, **details: Any) -> ToolResult:
    """Every failure a tool can anticipate goes through here.

    Tool failures are *information for the model*, not exceptions: it sees the
    message and can correct itself on the next turn. Raising instead would end
    the run over a mistyped path.
    """
    return ToolResult(content=text_blocks(content), is_error=True, details=details)
