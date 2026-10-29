"""Harness types - frozen dataclasses with __slots__ for immutability.

All harness types defined here for consistent imports and avoiding
circular dependencies. Types use:
- frozen dataclasses with __slots__ for immutability and memory efficiency
- Union types with None for optional fields
- Proper type hints throughout

Reference: catalog/core/types.py pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
)

__all__ = [
    "DIGEST_PATTERN",
    "ApprovalSpec",
    "BudgetSpec",
    "GuardSpec",
    "HarnessManifest",
    "RecoveryPolicy",
    "RuntimeCapabilities",
    "SubagentsSpec",
    "ToolSpec",
]

# Regex pattern for valid sha256 digests
DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[a-fA-F0-9]{64}$")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Individual tool specification within harness manifest.

    Attributes:
        tool_class: Classification determining recovery behavior
    """

    tool_class: ToolClass

    def body_for_digest(self) -> dict[str, str]:
        """Return canonical body for digest computation."""
        return {"tool_class": self.tool_class.value}


@dataclass(frozen=True, slots=True)
class BudgetSpec:
    """Resource budget constraints for session execution.

    Attributes:
        max_cost_per_session: Maximum cost in USD per session
        max_tokens_per_turn: Tokens (input and output, every model call) one run may use;
            past it the run ends at its next step, saying so, and the session goes on
        max_tokens_per_session: Tokens the whole session may use; past it the run ends,
            and each later message ends at once without asking the model (pi-temporal's
            session bound)
        max_seconds_per_session: Seconds the session's runs may take in all (idle time is
            free); past it, as for ``max_tokens_per_session``
    """

    max_cost_per_session: float | None = None
    max_tokens_per_turn: int | None = None
    max_tokens_per_session: int | None = None
    max_seconds_per_session: float | None = None

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation.

        The session bounds appear only when set, so every manifest published before them
        keeps its digest (and its pins).
        """
        body: dict[str, Any] = {
            "max_cost_per_session": self.max_cost_per_session,
            "max_tokens_per_turn": self.max_tokens_per_turn,
        }
        if self.max_tokens_per_session is not None:
            body["max_tokens_per_session"] = self.max_tokens_per_session
        if self.max_seconds_per_session is not None:
            body["max_seconds_per_session"] = self.max_seconds_per_session
        return body


@dataclass(frozen=True, slots=True)
class GuardSpec:
    """Guard rail configuration for agent behavior.

    Attributes:
        repetition: Behavior when repetition detected
        oscillation: Behavior when oscillation detected
        stall_iterations: Max iterations before stall guard triggers
    """

    repetition: GuardBehavior = GuardBehavior.ABORT
    oscillation: GuardBehavior = GuardBehavior.ABORT
    stall_iterations: int = 10

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation."""
        return {
            "repetition": self.repetition.value,
            "oscillation": self.oscillation.value,
            "stall_iterations": self.stall_iterations,
        }


@dataclass(frozen=True, slots=True)
class ApprovalSpec:
    """Human approval workflow configuration.

    Attributes:
        timeout_s: Seconds to wait for approval
        on_timeout: Behavior when approval times out
        required: Whether approval is required for all operations
        require_approval: Specific tool names requiring approval
    """

    timeout_s: float = 300.0
    on_timeout: ApprovalOnTimeout = ApprovalOnTimeout.DENY
    required: bool = False
    require_approval: tuple[str, ...] = ()

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation."""
        return {
            "timeout_s": self.timeout_s,
            "on_timeout": self.on_timeout.value,
            "required": self.required,
            "require_approval": list(self.require_approval),
        }


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """Recovery behavior policy for tool failures.

    Pinned per harness version. Unknown tools use ``default`` action.

    Attributes:
        default: Default recovery action for unknown tools
        by_tool_class: Recovery action overrides by tool class
        tool_classes: Tool name to class mappings
    """

    default: RecoveryAction
    by_tool_class: dict[ToolClass, RecoveryAction] = field(default_factory=dict)
    tool_classes: dict[str, ToolClass] = field(default_factory=dict)

    def classify(self, tool_name: str) -> ToolClass | None:
        """Get the tool class for a tool name."""
        return self.tool_classes.get(tool_name)

    def action_for(self, tool_name: str) -> RecoveryAction:
        """Get the recovery action for a tool name."""
        tool_class = self.classify(tool_name)
        if tool_class is None:
            return self.default
        return self.by_tool_class.get(tool_class, self.default)

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation."""
        return {
            "default": self.default.value,
            "by_tool_class": {k.value: v.value for k, v in sorted(self.by_tool_class.items())},
            "tool_classes": dict(sorted(self.tool_classes.items())),
        }


@dataclass(frozen=True, slots=True)
class SubagentsSpec:
    """Subagent spawning constraints.

    Harness ``subagents:`` block - max_depth is enforced at spawn.

    Attributes:
        max_depth: Maximum subagent nesting depth
        default_mode: Default execution mode for subagents
        allow_background: Whether background subagents are allowed
    """

    max_depth: int = 3
    default_mode: str = "one_shot"
    allow_background: bool = True

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation."""
        return {
            "max_depth": self.max_depth,
            "default_mode": self.default_mode,
            "allow_background": self.allow_background,
        }


@dataclass(frozen=True, slots=True)
class LoopSpec:
    """How long one run may go on, and how its end is handled (harness ``loop:`` block).

    Attributes:
        max_steps: Model calls one run may make
        warn_steps_remaining: From this many steps left, each request says how many remain
        force_answer_on_final_step: The last step offers no tools, so the run ends with
            an answer rather than at the limit with none
        max_run_seconds: Wall-clock limit for one run (None: no limit beyond the turn
            timeout); past it the run ends at its next step, saying so
        hard_run_seconds: A deadline for one run that stops it where it is, a running
            model or tool call included (pi-temporal's hard deadline); its calls are
            answered as unknown, and the session goes on
        model_retries: Times a model call that may pass (rate limit, overload, a dropped
            connection) is asked again, on a durable timer
        retry_base_seconds: The first wait; it doubles each retry
        retry_max_seconds: The longest wait, a provider's Retry-After included
    """

    max_steps: int = 30
    warn_steps_remaining: int = 3
    force_answer_on_final_step: bool = True
    max_run_seconds: float | None = None
    hard_run_seconds: float | None = None
    model_retries: int = 3
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation.

        Settings added later appear only when set away from their defaults, so every
        manifest published before them keeps its digest (and its pins).
        """
        body: dict[str, Any] = {
            "max_steps": self.max_steps,
            "warn_steps_remaining": self.warn_steps_remaining,
            "force_answer_on_final_step": self.force_answer_on_final_step,
        }
        defaults = LoopSpec()
        for name in (
            "max_run_seconds",
            "hard_run_seconds",
            "model_retries",
            "retry_base_seconds",
            "retry_max_seconds",
        ):
            if getattr(self, name) != getattr(defaults, name):
                body[name] = getattr(self, name)
        return body


@dataclass(frozen=True, slots=True)
class HarnessManifest:
    """Immutable, version-pinned specification for agent behavior.

    This is the core type representing a harness configuration.
    All fields are immutable after creation.

    Attributes:
        harness_version: Unique version identifier
        protocol_version: Wire protocol version (1, 2, etc.)
        runtime: Runtime implementation name (echo, harness, githi)
        requires_sandbox: Whether sandbox isolation is required
        mode: Session execution mode (sticky/stateless)
        image_digest: Docker image digest for sandboxed execution
        extension_bundle_uri: URI to extension bundle
        tools: Tool specifications by name
        recovery: Recovery policy configuration
        budget: Optional resource budget constraints
        guards: Optional guard rail configuration
        approvals: Optional approval workflow configuration
        subagents: Optional subagent constraints
        model: Optional default model name
        system_prompt_sha: Optional system prompt content hash
        loop: Optional run-length settings (``LoopSpec`` defaults when absent)
    """

    harness_version: str
    protocol_version: int
    runtime: str
    requires_sandbox: bool
    mode: RuntimeMode
    image_digest: str | None
    extension_bundle_uri: str | None
    tools: dict[str, ToolSpec]
    recovery: RecoveryPolicy
    budget: BudgetSpec | None = None
    guards: GuardSpec | None = None
    approvals: ApprovalSpec | None = None
    subagents: SubagentsSpec | None = None
    model: str | None = None
    system_prompt_sha: str | None = None
    loop: LoopSpec | None = None

    @property
    def version(self) -> str:
        """Alias used by older call sites."""
        return self.harness_version

    @property
    def max_subagent_depth(self) -> int:
        """Get maximum subagent nesting depth."""
        if self.subagents is not None and self.subagents.max_depth > 0:
            return self.subagents.max_depth
        return 3

    def body_for_digest(self) -> dict[str, Any]:
        """Return canonical body for digest computation.

        Excludes mutable fields and metadata.
        """
        return {
            "harness_version": self.harness_version,
            "protocol_version": self.protocol_version,
            "runtime": self.runtime,
            "requires_sandbox": self.requires_sandbox,
            "mode": self.mode.value,
            "image_digest": self.image_digest,
            "extension_bundle_uri": self.extension_bundle_uri,
            "tools": {k: v.body_for_digest() for k, v in sorted(self.tools.items())},
            "recovery": self.recovery.body_for_digest(),
            "budget": self.budget.body_for_digest() if self.budget else None,
            "guards": self.guards.body_for_digest() if self.guards else None,
            "approvals": self.approvals.body_for_digest() if self.approvals else None,
            "subagents": self.subagents.body_for_digest() if self.subagents else None,
            "model": self.model,
            "system_prompt_sha": self.system_prompt_sha,
            # Only when set: manifests written before ``loop`` keep their digests.
            **({"loop": self.loop.body_for_digest()} if self.loop else {}),
        }


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Declared capabilities of a harness runtime.

    Attributes:
        protocol_versions: Supported protocol versions
        commands: Supported RPC commands
        emits_settled_event: Whether runtime emits settled events
        supports_fork: Whether runtime supports forking
        supports_history: Whether runtime supports history queries
        supports_approvals: Whether runtime supports approval workflows
    """

    protocol_versions: frozenset[int]
    commands: frozenset[str]
    emits_settled_event: bool = False
    supports_fork: bool = False
    supports_history: bool = True
    supports_approvals: bool = False


def default_recovery_policy() -> RecoveryPolicy:
    """Return the default recovery policy for harness manifests.

    Default classifications:
    - PURE: read, grep, echo (auto-resume)
    - IDEMPOTENT: write (auto-resume)
    - TRANSACTIONAL: git_commit (needs review)
    - NON_ATOMIC: bash, npm, migrate (fail)
    """
    return RecoveryPolicy(
        default=RecoveryAction.FAIL,
        by_tool_class={
            ToolClass.PURE: RecoveryAction.AUTO_RESUME,
            ToolClass.IDEMPOTENT: RecoveryAction.AUTO_RESUME,
            ToolClass.TRANSACTIONAL: RecoveryAction.NEEDS_REVIEW,
            ToolClass.NON_ATOMIC: RecoveryAction.FAIL,
        },
        tool_classes={
            "read": ToolClass.PURE,
            "grep": ToolClass.PURE,
            "echo": ToolClass.PURE,
            "write": ToolClass.IDEMPOTENT,
            "git_commit": ToolClass.TRANSACTIONAL,
            "bash": ToolClass.NON_ATOMIC,
            "npm": ToolClass.NON_ATOMIC,
            "migrate": ToolClass.NON_ATOMIC,
        },
    )
