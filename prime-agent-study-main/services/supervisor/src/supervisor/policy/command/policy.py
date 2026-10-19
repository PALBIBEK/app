"""Command policy — evaluation against denylist/allowlist.

Implements QM-style command policy:
- DENYLIST mode: Allow all except matches
- ALLOWLIST mode: Deny all except matches

Reference: QM's command policy pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supervisor.policy.command.rules import DEFAULT_FLOOR_RULES
from supervisor.policy.command.scanner import CommandScanner
from supervisor.policy.errors import CommandDenied, CommandRequiresApproval
from supervisor.policy.types import CommandAction, CommandDecision, CommandPolicyMode, CommandRule


if TYPE_CHECKING:
    pass


__all__ = ["CommandPolicy"]


class CommandPolicy:
    """Evaluates commands against policy rules.

    Modes:
    - DENYLIST: Allow all commands except those matching deny rules
    - ALLOWLIST: Deny all commands except those matching allow rules

    Floor rules are always applied regardless of mode.

    Example:
        policy = CommandPolicy(
            mode=CommandPolicyMode.DENYLIST,
            rules=[custom_rule],
            use_floor_rules=True,
        )

        decision = policy.evaluate("rm -rf /")
        if not decision.allowed:
            if decision.requires_approval:
                # Ask for approval
                ...
            else:
                raise CommandDenied(...)
    """

    __slots__ = ("_mode", "_scanner", "_floor_scanner")

    def __init__(
        self,
        *,
        mode: CommandPolicyMode = CommandPolicyMode.DENYLIST,
        rules: list[CommandRule] | None = None,
        use_floor_rules: bool = True,
    ) -> None:
        """Initialize command policy.

        Args:
            mode: Policy mode (DENYLIST or ALLOWLIST)
            rules: Custom rules to apply
            use_floor_rules: Whether to apply floor safety rules
        """
        self._mode = mode
        self._scanner = CommandScanner(list(rules or []))

        # Floor rules are always checked first
        if use_floor_rules:
            self._floor_scanner = CommandScanner(list(DEFAULT_FLOOR_RULES))
        else:
            self._floor_scanner = CommandScanner([])

    def evaluate(self, command: str) -> CommandDecision:
        """Evaluate command against policy.

        Args:
            command: Command string to evaluate

        Returns:
            Decision indicating if command is allowed
        """
        # Check floor rules first (always enforced)
        floor_decision = self._floor_scanner.scan(command)
        if floor_decision is not None:
            return floor_decision

        # Check custom rules
        rule_decision = self._scanner.scan(command)

        if self._mode == CommandPolicyMode.DENYLIST:
            # Allow unless rule denies
            if rule_decision is not None:
                return rule_decision

            # No rule matched = allow
            return CommandDecision(
                allowed=True,
                requires_approval=False,
                rule_matched=None,
                reason=None,
                action=CommandAction.ALLOW,
            )

        else:  # ALLOWLIST mode
            # Deny unless rule allows
            if rule_decision is not None and rule_decision.action == CommandAction.ALLOW:
                return rule_decision

            # No allow rule matched = deny
            return CommandDecision(
                allowed=False,
                requires_approval=False,
                rule_matched=None,
                reason="Command not in allowlist",
                action=CommandAction.DENY,
            )

    def check(self, command: str) -> None:
        """Check command and raise if not allowed.

        Args:
            command: Command to check

        Raises:
            CommandDenied: If command is denied
            CommandRequiresApproval: If command requires approval
        """
        decision = self.evaluate(command)

        if decision.requires_approval:
            raise CommandRequiresApproval(
                command=command,
                rule=decision.rule_matched or "",
                reason=decision.reason or "Requires approval",
            )

        if not decision.allowed:
            raise CommandDenied(
                command=command,
                rule=decision.rule_matched or "",
                reason=decision.reason or "Command denied",
            )

    def add_rule(
        self,
        pattern: str,
        action: CommandAction,
        reason: str,
        *,
        priority: int = 0,
    ) -> None:
        """Add custom rule.

        Args:
            pattern: Regex pattern
            action: Action on match
            reason: Human-readable reason
            priority: Rule priority
        """
        rule = CommandRule(
            pattern=pattern,
            action=action,
            reason=reason,
            priority=priority,
            case_sensitive=False,
        )
        self._scanner.add_rule(rule)

    @property
    def mode(self) -> CommandPolicyMode:
        """Current policy mode."""
        return self._mode

    @property
    def rule_count(self) -> int:
        """Number of custom rules."""
        return self._scanner.rule_count

    @property
    def floor_rule_count(self) -> int:
        """Number of floor rules."""
        return self._floor_scanner.rule_count
