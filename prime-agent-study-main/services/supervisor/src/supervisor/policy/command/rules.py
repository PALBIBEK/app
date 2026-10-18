"""Command rules — default floor rules and rule creation.

Provides safety floor rules that should always be enforced:
- Destructive file operations (rm -rf)
- Dangerous git operations (force push)
- Destructive SQL (DROP TABLE)
- System-level commands (mkfs)
- Suspicious patterns (curl | bash)

Reference: QM's command policy rules.
"""

from __future__ import annotations

import re
from typing import Final

from supervisor.policy.types import CommandAction, CommandRule


__all__ = [
    "DEFAULT_FLOOR_RULES",
    "create_rule",
]


def create_rule(
    pattern: str,
    action: CommandAction,
    reason: str,
    *,
    priority: int = 0,
    case_sensitive: bool = False,
) -> CommandRule:
    """Create a command rule.

    Args:
        pattern: Regex pattern to match
        action: Action to take on match
        reason: Human-readable reason for rule
        priority: Rule priority (higher = checked first)
        case_sensitive: Whether pattern is case-sensitive

    Returns:
        CommandRule instance
    """
    return CommandRule(
        pattern=pattern,
        action=action,
        reason=reason,
        priority=priority,
        case_sensitive=case_sensitive,
    )


# Safety floor rules — always enforced
DEFAULT_FLOOR_RULES: Final[tuple[CommandRule, ...]] = (
    # Destructive file operations
    create_rule(
        pattern=r"rm\s+(-[rfR]+\s+)*(/|~|\.\.)",
        action=CommandAction.DENY,
        reason="Recursive delete of system/home directory",
        priority=100,
    ),
    create_rule(
        pattern=r"rm\s+.*(-r|-R|--recursive)",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Recursive delete requires approval",
        priority=50,
    ),
    create_rule(
        pattern=r"rmdir\s+--ignore-fail-on-non-empty",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Force directory removal",
        priority=50,
    ),

    # Dangerous git operations
    create_rule(
        pattern=r"git\s+push\s+.*(-f|--force)",
        action=CommandAction.DENY,
        reason="Force push can destroy remote history",
        priority=100,
    ),
    create_rule(
        pattern=r"git\s+push\s+.*--delete",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Deleting remote branch",
        priority=50,
    ),
    create_rule(
        pattern=r"git\s+reset\s+--hard",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Hard reset discards changes",
        priority=50,
    ),

    # Destructive SQL
    create_rule(
        pattern=r"DROP\s+(TABLE|DATABASE|SCHEMA)",
        action=CommandAction.DENY,
        reason="Destructive SQL operation",
        priority=100,
    ),
    create_rule(
        pattern=r"TRUNCATE\s+TABLE",
        action=CommandAction.DENY,
        reason="Destructive SQL operation",
        priority=100,
    ),
    create_rule(
        pattern=r"DELETE\s+FROM\s+\w+\s*($|;|\s+WHERE\s+1\s*=\s*1)",
        action=CommandAction.DENY,
        reason="Unbounded DELETE",
        priority=100,
    ),

    # System-level commands
    create_rule(
        pattern=r"mkfs",
        action=CommandAction.DENY,
        reason="Filesystem format command",
        priority=100,
    ),
    create_rule(
        pattern=r"dd\s+.*of=/dev/",
        action=CommandAction.DENY,
        reason="Direct disk write",
        priority=100,
    ),
    create_rule(
        pattern=r"sudo\s+rm\s+",
        action=CommandAction.DENY,
        reason="Privileged delete",
        priority=100,
    ),
    create_rule(
        pattern=r"chmod\s+(-R\s+)?777",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="World-writable permissions",
        priority=50,
    ),

    # Suspicious patterns
    create_rule(
        pattern=r"curl.*\|\s*(sh|bash|zsh)",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Pipe to shell execution",
        priority=75,
    ),
    create_rule(
        pattern=r"wget.*\|\s*(sh|bash|zsh)",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Pipe to shell execution",
        priority=75,
    ),
    create_rule(
        pattern=r"eval\s+.*\$\(",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Dynamic eval with command substitution",
        priority=75,
    ),

    # Credential exposure
    create_rule(
        pattern=r"echo.*\$\{?(AWS_SECRET|API_KEY|PASSWORD|SECRET)",
        action=CommandAction.DENY,
        reason="Potential credential exposure",
        priority=100,
    ),
    create_rule(
        pattern=r"env\s*$",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Environment dump may expose secrets",
        priority=50,
    ),

    # Network operations that could exfiltrate
    create_rule(
        pattern=r"scp\s+.*@.*:",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Remote file transfer",
        priority=50,
    ),
    create_rule(
        pattern=r"rsync\s+.*@.*:",
        action=CommandAction.REQUIRE_APPROVAL,
        reason="Remote file sync",
        priority=50,
    ),
)
