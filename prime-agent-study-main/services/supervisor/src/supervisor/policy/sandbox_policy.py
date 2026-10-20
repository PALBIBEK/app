"""Sandbox policy — file effects and approval defaults per session.

Rewritten with:
- Enums from types.py instead of strings
- Dynamic tool registry (not hardcoded)
- Integration with command policy
- Proper slots and typing

Reference: QM's sandbox policy pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from supervisor.policy.types import (
    ALLOW_CHOICES,
    ApprovalDefault,
    FileEffects,
)

if TYPE_CHECKING:
    from supervisor.policy.command import CommandPolicy


__all__ = [
    "TOOL_CLASSES",
    "SandboxPolicy",
    "SandboxPolicyConfig",
    "ToolClass",
]


# =============================================================================
# Tool Classification
# =============================================================================

class ToolClass:
    """Tool classifications for permission checking."""
    PURE = "pure"               # No side effects (read, echo)
    READ = "read"               # Read-only filesystem access
    WRITE = "write"             # Workspace writes
    IDEMPOTENT = "idempotent"   # Safe to retry
    TRANSACTIONAL = "transactional"  # Atomic operations
    NON_ATOMIC = "non_atomic"   # Dangerous, may leave partial state
    SHELL = "shell"             # Shell execution


# Default tool classifications
TOOL_CLASSES: Final[dict[str, str]] = {
    # Pure tools (no side effects)
    "echo": ToolClass.PURE,
    "list": ToolClass.PURE,

    # Read-only tools
    "read": ToolClass.READ,
    "grep": ToolClass.READ,
    "search": ToolClass.READ,
    "glob": ToolClass.READ,
    "cat": ToolClass.READ,
    "head": ToolClass.READ,
    "tail": ToolClass.READ,
    "find": ToolClass.READ,
    "ls": ToolClass.READ,

    # Write tools (workspace-safe)
    "write": ToolClass.WRITE,
    "edit": ToolClass.WRITE,
    "patch": ToolClass.WRITE,
    "git_commit": ToolClass.WRITE,
    "mkdir": ToolClass.WRITE,
    "touch": ToolClass.WRITE,
    "mv": ToolClass.WRITE,
    "cp": ToolClass.WRITE,
    "rm": ToolClass.WRITE,

    # Danger tools (require full access)
    "bash": ToolClass.SHELL,
    "shell": ToolClass.SHELL,
    "npm": ToolClass.NON_ATOMIC,
    "pip": ToolClass.NON_ATOMIC,
    "migrate": ToolClass.NON_ATOMIC,
    "pty": ToolClass.SHELL,
}


# =============================================================================
# Configuration
# =============================================================================

@dataclass(frozen=True, slots=True)
class SandboxPolicyConfig:
    """Per-session sandbox policy configuration."""
    file_effects: FileEffects = FileEffects.WORKSPACE_WRITE
    approval_default: ApprovalDefault = ApprovalDefault.ASK
    # The session's sandbox confines commands (a container or a VM), not the host.
    isolated: bool = False


ISOLATING_PROVIDERS: Final[frozenset[str]] = frozenset({"local_docker", "e2b"})
"""Providers whose sandboxes confine whatever a command does to the sandbox."""


# Default configuration
DEFAULT_CONFIG: Final[SandboxPolicyConfig] = SandboxPolicyConfig()


# =============================================================================
# Sandbox Policy
# =============================================================================

class SandboxPolicy:
    """Per-session policy from sandbox profile.

    Controls:
    - Which tools are allowed based on file_effects
    - Whether approvals are auto-granted

    Example:
        policy = SandboxPolicy()

        # Configure from profile
        policy.configure("sess_123", SandboxPolicyConfig(
            file_effects=FileEffects.READ_ONLY,
            approval_default=ApprovalDefault.ASK,
        ))

        # Check tool
        denial = policy.check_tool_invoke("sess_123", "bash")
        if denial:
            raise PermissionDenied(denial)
    """

    __slots__ = ("_command_policy", "_configs", "_tool_classes")

    def __init__(
        self,
        *,
        command_policy: CommandPolicy | None = None,
    ) -> None:
        self._configs: dict[str, SandboxPolicyConfig] = {}
        self._tool_classes: dict[str, str] = dict(TOOL_CLASSES)
        self._command_policy = command_policy

    def configure(
        self,
        session_id: str,
        config: Any | None = None,
        *,
        isolated: bool = False,
    ) -> None:
        """Configure policy for session.

        Args:
            session_id: Session to configure
            config: A policy config or a sandbox profile (defaults to DEFAULT_CONFIG)
            isolated: The session's sandbox isolates commands from the host
        """
        base = config or DEFAULT_CONFIG
        self._configs[session_id] = SandboxPolicyConfig(
            file_effects=FileEffects(_value(base.file_effects)),
            approval_default=ApprovalDefault(_value(base.approval_default)),
            isolated=isolated,
        )

    def configure_from_profile(
        self,
        session_id: str,
        file_effects: str | None = None,
        approval_default: str | None = None,
    ) -> None:
        """Configure from profile strings (backward compatibility).

        Args:
            session_id: Session to configure
            file_effects: File effects string
            approval_default: Approval default string
        """
        fe = FileEffects(file_effects) if file_effects else FileEffects.WORKSPACE_WRITE
        ad = ApprovalDefault(approval_default) if approval_default else ApprovalDefault.ASK

        self._configs[session_id] = SandboxPolicyConfig(
            file_effects=fe,
            approval_default=ad,
        )

    def clear(self, session_id: str) -> None:
        """Clear configuration for session.

        Args:
            session_id: Session to clear
        """
        self._configs.pop(session_id, None)

    def config_for(self, session_id: str) -> SandboxPolicyConfig:
        """Get configuration for session.

        Args:
            session_id: Session to get config for

        Returns:
            Session config (or default)
        """
        return self._configs.get(session_id, DEFAULT_CONFIG)

    def register_tool_class(self, tool_name: str, tool_class: str) -> None:
        """Register tool classification.

        Args:
            tool_name: Name of tool
            tool_class: Classification (use ToolClass constants)
        """
        self._tool_classes[tool_name.lower()] = tool_class

    def check_tool_invoke(
        self,
        session_id: str,
        tool_name: str,
        *,
        tool_class: str | None = None,
        command: str | None = None,
    ) -> str | None:
        """Check if tool invocation is allowed.

        Args:
            session_id: Session making call
            tool_name: Tool being invoked
            tool_class: Optional explicit tool class
            command: Optional command string for shell tools

        Returns:
            Denial reason if blocked, None if allowed
        """
        config = self.config_for(session_id)
        name = tool_name.lower()

        # Get tool class
        tc = tool_class or self._tool_classes.get(name, ToolClass.WRITE)
        tc = tc.lower()

        # Full access allows everything
        if config.file_effects == FileEffects.DANGER_FULL_ACCESS:
            return None

        # Read-only mode
        if config.file_effects == FileEffects.READ_ONLY:
            if tc in (ToolClass.PURE, ToolClass.READ):
                return None
            return (
                f"tool {tool_name!r} blocked by sandbox profile "
                f"(file_effects=read_only)"
            )

        # Workspace write mode
        # Allow read/write/idempotent/transactional
        if tc in (ToolClass.PURE, ToolClass.READ, ToolClass.WRITE,
                  ToolClass.IDEMPOTENT, ToolClass.TRANSACTIONAL):
            return None

        # Shell/non-atomic: inside an isolating sandbox a command can only change the
        # sandbox, as the file tools can; on the host it could change anything.
        if tc in (ToolClass.SHELL, ToolClass.NON_ATOMIC) and not config.isolated:
            return (
                f"tool {tool_name!r} blocked by sandbox profile "
                f"(file_effects=workspace_write on a host-process sandbox; "
                f"needs an isolating sandbox or danger_full_access)"
            )

        # Default: allow
        return None

    def auto_approve_choice(
        self,
        session_id: str,
        options: list[str],
    ) -> str | None:
        """Get auto-approval choice if approval_default is NEVER.

        Args:
            session_id: Session to check
            options: Available options

        Returns:
            Choice to use, or None if not auto-approving
        """
        config = self.config_for(session_id)

        if config.approval_default != ApprovalDefault.NEVER:
            return None

        # Find allow-shaped option
        if not options:
            return "allow"

        for opt in options:
            if opt.lower() in ALLOW_CHOICES:
                return opt

        # Default to first option
        return options[0]

    def should_auto_approve(self, session_id: str) -> bool:
        """Check if session auto-approves.

        Args:
            session_id: Session to check

        Returns:
            True if approval_default is NEVER
        """
        return self.config_for(session_id).approval_default == ApprovalDefault.NEVER

    def should_always_ask(self, session_id: str) -> bool:
        """Check if session always asks for approval.

        Args:
            session_id: Session to check

        Returns:
            True if approval_default is ALWAYS
        """
        return self.config_for(session_id).approval_default == ApprovalDefault.ALWAYS



def _value(enum_or_str: Any) -> str:
    return str(getattr(enum_or_str, "value", enum_or_str))
