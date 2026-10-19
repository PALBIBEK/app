"""Security classifiers — regex and heuristic-based content analysis.

Multi-layer classification:
1. RegexClassifier: Fast pattern matching (always runs first)
2. HeuristicClassifier: Rule-based analysis for edge cases
3. LLMClassifier: (placeholder) LLM-based classification

Reference: QM's security-screener.ts pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from supervisor.policy.security.types import (
    ThreatLevel,
)

__all__ = [
    "DANGEROUS_PATTERNS",
    "SUSPICIOUS_PATTERNS",
]


# =============================================================================
# Pattern Definitions
# =============================================================================

@dataclass(frozen=True, slots=True)
class PatternDef:
    """Pattern definition with metadata."""
    pattern: re.Pattern[str]
    category: str
    severity: ThreatLevel
    reason: str


def _compile_pattern(pattern: str, flags: int = 0) -> re.Pattern[str]:
    """Compile pattern with error handling."""
    return re.compile(pattern, flags)


# Dangerous patterns - should block
DANGEROUS_PATTERNS: Final[tuple[PatternDef, ...]] = (
    # Destructive file operations
    PatternDef(
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+/", re.IGNORECASE),
        "destructive",
        ThreatLevel.CRITICAL,
        "Recursive delete from root",
    ),
    PatternDef(
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+~", re.IGNORECASE),
        "destructive",
        ThreatLevel.CRITICAL,
        "Recursive delete from home",
    ),
    PatternDef(
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+\.\.", re.IGNORECASE),
        "destructive",
        ThreatLevel.HIGH,
        "Recursive delete parent directory",
    ),
    PatternDef(
        _compile_pattern(r":(){ :\|:& };:", re.IGNORECASE),
        "destructive",
        ThreatLevel.CRITICAL,
        "Fork bomb",
    ),
    PatternDef(
        _compile_pattern(r"mkfs\s+", re.IGNORECASE),
        "destructive",
        ThreatLevel.CRITICAL,
        "Filesystem format",
    ),
    PatternDef(
        _compile_pattern(r"dd\s+.*of=/dev/", re.IGNORECASE),
        "destructive",
        ThreatLevel.CRITICAL,
        "Direct disk write",
    ),

    # Dangerous git operations
    PatternDef(
        _compile_pattern(r"git\s+push\s+.*(-f|--force)", re.IGNORECASE),
        "git_danger",
        ThreatLevel.HIGH,
        "Force push",
    ),
    PatternDef(
        _compile_pattern(r"git\s+reset\s+--hard", re.IGNORECASE),
        "git_danger",
        ThreatLevel.MEDIUM,
        "Hard reset",
    ),

    # SQL injection / destructive SQL
    PatternDef(
        _compile_pattern(r"DROP\s+(TABLE|DATABASE|INDEX|VIEW)", re.IGNORECASE),
        "sql_danger",
        ThreatLevel.CRITICAL,
        "Destructive SQL DROP",
    ),
    PatternDef(
        _compile_pattern(r"TRUNCATE\s+TABLE", re.IGNORECASE),
        "sql_danger",
        ThreatLevel.HIGH,
        "Destructive SQL TRUNCATE",
    ),
    PatternDef(
        _compile_pattern(r"DELETE\s+FROM\s+\w+\s*(?:WHERE\s+1\s*=\s*1|;|$)", re.IGNORECASE),
        "sql_danger",
        ThreatLevel.HIGH,
        "Unbounded DELETE",
    ),

    # Remote code execution
    PatternDef(
        _compile_pattern(r"curl.*\|\s*(bash|sh|python|perl|ruby)", re.IGNORECASE),
        "rce",
        ThreatLevel.CRITICAL,
        "Pipe to shell",
    ),
    PatternDef(
        _compile_pattern(r"wget.*\|\s*(bash|sh|python|perl|ruby)", re.IGNORECASE),
        "rce",
        ThreatLevel.CRITICAL,
        "Pipe to shell",
    ),
    PatternDef(
        _compile_pattern(r"eval\s*\([^)]*\$", re.IGNORECASE),
        "rce",
        ThreatLevel.HIGH,
        "Dynamic eval with variable",
    ),
    PatternDef(
        _compile_pattern(r"exec\s*\([^)]*\$", re.IGNORECASE),
        "rce",
        ThreatLevel.HIGH,
        "Dynamic exec with variable",
    ),

    # Python dangerous patterns
    PatternDef(
        _compile_pattern(r"__import__\s*\(\s*['\"]os['\"]", re.IGNORECASE),
        "python_danger",
        ThreatLevel.HIGH,
        "Dynamic os import",
    ),
    PatternDef(
        _compile_pattern(r"__import__\s*\(\s*['\"]subprocess['\"]", re.IGNORECASE),
        "python_danger",
        ThreatLevel.HIGH,
        "Dynamic subprocess import",
    ),
    PatternDef(
        _compile_pattern(r"os\.system\s*\(", re.IGNORECASE),
        "python_danger",
        ThreatLevel.MEDIUM,
        "os.system call",
    ),
    PatternDef(
        _compile_pattern(r"subprocess\.(call|run|Popen)\s*\(\s*['\"]", re.IGNORECASE),
        "python_danger",
        ThreatLevel.MEDIUM,
        "subprocess shell call",
    ),

    # Data exfiltration
    PatternDef(
        _compile_pattern(r"curl.*-d.*@", re.IGNORECASE),
        "exfiltration",
        ThreatLevel.HIGH,
        "Curl file upload",
    ),
    PatternDef(
        _compile_pattern(r"scp\s+.*@.*:", re.IGNORECASE),
        "exfiltration",
        ThreatLevel.MEDIUM,
        "SCP to remote",
    ),

    # Credential exposure
    PatternDef(
        _compile_pattern(
            r"(password|passwd|secret|api_key|apikey|token|auth)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
            re.IGNORECASE,
        ),
        "credential",
        ThreatLevel.HIGH,
        "Hardcoded credential",
    ),
    PatternDef(
        _compile_pattern(r"AWS_SECRET_ACCESS_KEY\s*=", re.IGNORECASE),
        "credential",
        ThreatLevel.CRITICAL,
        "AWS secret key",
    ),
    PatternDef(
        _compile_pattern(r"GITHUB_TOKEN\s*=", re.IGNORECASE),
        "credential",
        ThreatLevel.HIGH,
        "GitHub token",
    ),

    # Network attacks
    PatternDef(
        _compile_pattern(r"nc\s+-e\s+/bin/(ba)?sh", re.IGNORECASE),
        "network_attack",
        ThreatLevel.CRITICAL,
        "Netcat reverse shell",
    ),
    PatternDef(
        _compile_pattern(r"nmap\s+", re.IGNORECASE),
        "network_attack",
        ThreatLevel.MEDIUM,
        "Network scanning",
    ),
)


# Suspicious patterns - should warn/review
SUSPICIOUS_PATTERNS: Final[tuple[PatternDef, ...]] = (
    PatternDef(
        _compile_pattern(r"chmod\s+777", re.IGNORECASE),
        "permission",
        ThreatLevel.LOW,
        "World-writable permission",
    ),
    PatternDef(
        _compile_pattern(r"sudo\s+", re.IGNORECASE),
        "privilege",
        ThreatLevel.LOW,
        "Sudo usage",
    ),
    PatternDef(
        _compile_pattern(r"ssh\s+-o\s+StrictHostKeyChecking=no", re.IGNORECASE),
        "network",
        ThreatLevel.MEDIUM,
        "Disabled SSH host key checking",
    ),
    PatternDef(
        _compile_pattern(r"--insecure|--no-check-certificate", re.IGNORECASE),
        "network",
        ThreatLevel.MEDIUM,
        "Disabled SSL verification",
    ),
    PatternDef(
        _compile_pattern(r"base64\s+-d", re.IGNORECASE),
        "obfuscation",
        ThreatLevel.LOW,
        "Base64 decode",
    ),
)


# =============================================================================
# Regex Classifier
# =============================================================================

# =============================================================================
# Heuristic Classifier
# =============================================================================

# =============================================================================
# LLM Classifier (Placeholder)
# =============================================================================

