"""Command scanner — pattern matching for command evaluation.

Provides efficient pattern matching against command strings:
- Compiled regex patterns
- Priority-ordered matching
- Case sensitivity options
"""

from __future__ import annotations

import re

from supervisor.policy.types import CommandAction, CommandDecision, CommandRule

__all__ = ["CommandScanner"]


class CommandScanner:
    """Scans commands against compiled patterns.

    Compiles patterns once for efficient reuse.
    Matches in priority order (highest first).

    Example:
        scanner = CommandScanner([
            CommandRule(pattern=r"rm -rf", action=CommandAction.DENY, ...),
        ])

        decision = scanner.scan("rm -rf /")
        if decision and not decision.allowed:
            raise CommandDenied(...)
    """

    __slots__ = ("_compiled", "_rules")

    def __init__(self, rules: list[CommandRule]) -> None:
        """Initialize scanner with rules.

        Args:
            rules: Rules to compile (sorted by priority desc)
        """
        # Sort by priority descending
        self._rules = sorted(rules, key=lambda r: -r.priority)
        self._compiled: list[tuple[re.Pattern[str], CommandRule]] = []

        for rule in self._rules:
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(rule.pattern, flags)
                self._compiled.append((pattern, rule))
            except re.error:
                # Skip invalid patterns
                pass

    def scan(self, command: str) -> CommandDecision | None:
        """Scan command against rules.

        Args:
            command: Command string to check

        Returns:
            Decision if rule matches, None if no match
        """
        for pattern, rule in self._compiled:
            if pattern.search(command):
                return CommandDecision(
                    allowed=rule.action == CommandAction.ALLOW,
                    requires_approval=rule.action == CommandAction.REQUIRE_APPROVAL,
                    rule_matched=rule.pattern,
                    reason=rule.reason,
                    action=rule.action,
                )

        return None

    def add_rule(self, rule: CommandRule) -> None:
        """Add a rule to the scanner.

        Re-sorts and re-compiles rules.

        Args:
            rule: Rule to add
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: -r.priority)

        # Recompile
        self._compiled = []
        for r in self._rules:
            flags = 0 if r.case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(r.pattern, flags)
                self._compiled.append((pattern, r))
            except re.error:
                pass

    @property
    def rule_count(self) -> int:
        """Number of rules in scanner."""
        return len(self._compiled)
