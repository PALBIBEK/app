"""Security classifiers — regex and heuristic-based content analysis.

Multi-layer classification:
1. RegexClassifier: Fast pattern matching (always runs first)
2. HeuristicClassifier: Rule-based analysis for edge cases
3. LLMClassifier: (placeholder) LLM-based classification

Reference: QM's security-screener.ts pattern.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from supervisor.policy.security.types import (
    ClassifierType,
    ContentType,
    PatternMatch,
    SecurityVerdict,
    ThreatLevel,
)


__all__ = [
    "RegexClassifier",
    "HeuristicClassifier",
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
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+/", re.I),
        "destructive",
        ThreatLevel.CRITICAL,
        "Recursive delete from root",
    ),
    PatternDef(
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+~", re.I),
        "destructive",
        ThreatLevel.CRITICAL,
        "Recursive delete from home",
    ),
    PatternDef(
        _compile_pattern(r"rm\s+(-rf?|--recursive)\s+\.\.", re.I),
        "destructive",
        ThreatLevel.HIGH,
        "Recursive delete parent directory",
    ),
    PatternDef(
        _compile_pattern(r":(){ :\|:& };:", re.I),
        "destructive",
        ThreatLevel.CRITICAL,
        "Fork bomb",
    ),
    PatternDef(
        _compile_pattern(r"mkfs\s+", re.I),
        "destructive",
        ThreatLevel.CRITICAL,
        "Filesystem format",
    ),
    PatternDef(
        _compile_pattern(r"dd\s+.*of=/dev/", re.I),
        "destructive",
        ThreatLevel.CRITICAL,
        "Direct disk write",
    ),

    # Dangerous git operations
    PatternDef(
        _compile_pattern(r"git\s+push\s+.*(-f|--force)", re.I),
        "git_danger",
        ThreatLevel.HIGH,
        "Force push",
    ),
    PatternDef(
        _compile_pattern(r"git\s+reset\s+--hard", re.I),
        "git_danger",
        ThreatLevel.MEDIUM,
        "Hard reset",
    ),

    # SQL injection / destructive SQL
    PatternDef(
        _compile_pattern(r"DROP\s+(TABLE|DATABASE|INDEX|VIEW)", re.I),
        "sql_danger",
        ThreatLevel.CRITICAL,
        "Destructive SQL DROP",
    ),
    PatternDef(
        _compile_pattern(r"TRUNCATE\s+TABLE", re.I),
        "sql_danger",
        ThreatLevel.HIGH,
        "Destructive SQL TRUNCATE",
    ),
    PatternDef(
        _compile_pattern(r"DELETE\s+FROM\s+\w+\s*(?:WHERE\s+1\s*=\s*1|;|$)", re.I),
        "sql_danger",
        ThreatLevel.HIGH,
        "Unbounded DELETE",
    ),

    # Remote code execution
    PatternDef(
        _compile_pattern(r"curl.*\|\s*(bash|sh|python|perl|ruby)", re.I),
        "rce",
        ThreatLevel.CRITICAL,
        "Pipe to shell",
    ),
    PatternDef(
        _compile_pattern(r"wget.*\|\s*(bash|sh|python|perl|ruby)", re.I),
        "rce",
        ThreatLevel.CRITICAL,
        "Pipe to shell",
    ),
    PatternDef(
        _compile_pattern(r"eval\s*\([^)]*\$", re.I),
        "rce",
        ThreatLevel.HIGH,
        "Dynamic eval with variable",
    ),
    PatternDef(
        _compile_pattern(r"exec\s*\([^)]*\$", re.I),
        "rce",
        ThreatLevel.HIGH,
        "Dynamic exec with variable",
    ),

    # Python dangerous patterns
    PatternDef(
        _compile_pattern(r"__import__\s*\(\s*['\"]os['\"]", re.I),
        "python_danger",
        ThreatLevel.HIGH,
        "Dynamic os import",
    ),
    PatternDef(
        _compile_pattern(r"__import__\s*\(\s*['\"]subprocess['\"]", re.I),
        "python_danger",
        ThreatLevel.HIGH,
        "Dynamic subprocess import",
    ),
    PatternDef(
        _compile_pattern(r"os\.system\s*\(", re.I),
        "python_danger",
        ThreatLevel.MEDIUM,
        "os.system call",
    ),
    PatternDef(
        _compile_pattern(r"subprocess\.(call|run|Popen)\s*\(\s*['\"]", re.I),
        "python_danger",
        ThreatLevel.MEDIUM,
        "subprocess shell call",
    ),

    # Data exfiltration
    PatternDef(
        _compile_pattern(r"curl.*-d.*@", re.I),
        "exfiltration",
        ThreatLevel.HIGH,
        "Curl file upload",
    ),
    PatternDef(
        _compile_pattern(r"scp\s+.*@.*:", re.I),
        "exfiltration",
        ThreatLevel.MEDIUM,
        "SCP to remote",
    ),

    # Credential exposure
    PatternDef(
        _compile_pattern(
            r"(password|passwd|secret|api_key|apikey|token|auth)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
            re.I,
        ),
        "credential",
        ThreatLevel.HIGH,
        "Hardcoded credential",
    ),
    PatternDef(
        _compile_pattern(r"AWS_SECRET_ACCESS_KEY\s*=", re.I),
        "credential",
        ThreatLevel.CRITICAL,
        "AWS secret key",
    ),
    PatternDef(
        _compile_pattern(r"GITHUB_TOKEN\s*=", re.I),
        "credential",
        ThreatLevel.HIGH,
        "GitHub token",
    ),

    # Network attacks
    PatternDef(
        _compile_pattern(r"nc\s+-e\s+/bin/(ba)?sh", re.I),
        "network_attack",
        ThreatLevel.CRITICAL,
        "Netcat reverse shell",
    ),
    PatternDef(
        _compile_pattern(r"nmap\s+", re.I),
        "network_attack",
        ThreatLevel.MEDIUM,
        "Network scanning",
    ),
)


# Suspicious patterns - should warn/review
SUSPICIOUS_PATTERNS: Final[tuple[PatternDef, ...]] = (
    PatternDef(
        _compile_pattern(r"chmod\s+777", re.I),
        "permission",
        ThreatLevel.LOW,
        "World-writable permission",
    ),
    PatternDef(
        _compile_pattern(r"sudo\s+", re.I),
        "privilege",
        ThreatLevel.LOW,
        "Sudo usage",
    ),
    PatternDef(
        _compile_pattern(r"ssh\s+-o\s+StrictHostKeyChecking=no", re.I),
        "network",
        ThreatLevel.MEDIUM,
        "Disabled SSH host key checking",
    ),
    PatternDef(
        _compile_pattern(r"--insecure|--no-check-certificate", re.I),
        "network",
        ThreatLevel.MEDIUM,
        "Disabled SSL verification",
    ),
    PatternDef(
        _compile_pattern(r"base64\s+-d", re.I),
        "obfuscation",
        ThreatLevel.LOW,
        "Base64 decode",
    ),
)


# =============================================================================
# Regex Classifier
# =============================================================================

class RegexClassifier:
    """Fast regex-based pattern matching.

    Always runs first due to speed. Catches known dangerous patterns.

    Example:
        classifier = RegexClassifier()
        verdict = classifier.classify("rm -rf /")
        if not verdict.allowed:
            print(f"Blocked: {verdict.reason}")
    """

    __slots__ = ("_dangerous", "_suspicious", "_include_suspicious")

    def __init__(
        self,
        *,
        dangerous_patterns: tuple[PatternDef, ...] = DANGEROUS_PATTERNS,
        suspicious_patterns: tuple[PatternDef, ...] = SUSPICIOUS_PATTERNS,
        include_suspicious: bool = True,
    ) -> None:
        """Initialize regex classifier.

        Args:
            dangerous_patterns: Patterns that should block
            suspicious_patterns: Patterns that should warn
            include_suspicious: Whether to include suspicious patterns
        """
        self._dangerous = dangerous_patterns
        self._suspicious = suspicious_patterns if include_suspicious else ()
        self._include_suspicious = include_suspicious

    def classify(self, content: str) -> SecurityVerdict:
        """Classify content using regex patterns.

        Args:
            content: Content to classify

        Returns:
            SecurityVerdict with classification result
        """
        # Check dangerous patterns first
        for pattern_def in self._dangerous:
            match = pattern_def.pattern.search(content)
            if match:
                pattern_match = PatternMatch(
                    pattern=pattern_def.pattern.pattern,
                    category=pattern_def.category,
                    severity=pattern_def.severity,
                    matched_text=match.group()[:50],
                    start_pos=match.start(),
                    end_pos=match.end(),
                )
                return SecurityVerdict.blocked(
                    pattern_def.reason,
                    score=self._severity_to_score(pattern_def.severity),
                    threat_level=pattern_def.severity,
                    classifier=ClassifierType.REGEX,
                    patterns=(pattern_match,),
                )

        # Check suspicious patterns
        suspicious_matches: list[PatternMatch] = []
        max_severity = ThreatLevel.SAFE

        for pattern_def in self._suspicious:
            match = pattern_def.pattern.search(content)
            if match:
                pattern_match = PatternMatch(
                    pattern=pattern_def.pattern.pattern,
                    category=pattern_def.category,
                    severity=pattern_def.severity,
                    matched_text=match.group()[:50],
                    start_pos=match.start(),
                    end_pos=match.end(),
                )
                suspicious_matches.append(pattern_match)
                if self._severity_rank(pattern_def.severity) > self._severity_rank(max_severity):
                    max_severity = pattern_def.severity

        # If suspicious patterns found, return with warning
        if suspicious_matches:
            return SecurityVerdict(
                allowed=True,  # Allow but flag
                score=self._severity_to_score(max_severity),
                threat_level=max_severity,
                classifier=ClassifierType.REGEX,
                reason=f"Suspicious patterns: {len(suspicious_matches)} found",
                patterns_matched=tuple(suspicious_matches),
            )

        # All clear
        return SecurityVerdict.safe(classifier=ClassifierType.REGEX)

    def _severity_to_score(self, severity: ThreatLevel) -> Decimal:
        """Convert severity to numeric score."""
        scores = {
            ThreatLevel.SAFE: Decimal("0.0"),
            ThreatLevel.LOW: Decimal("0.3"),
            ThreatLevel.MEDIUM: Decimal("0.5"),
            ThreatLevel.HIGH: Decimal("0.8"),
            ThreatLevel.CRITICAL: Decimal("1.0"),
        }
        return scores.get(severity, Decimal("0.5"))

    def _severity_rank(self, severity: ThreatLevel) -> int:
        """Get numeric rank for severity comparison."""
        ranks = {
            ThreatLevel.SAFE: 0,
            ThreatLevel.LOW: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.HIGH: 3,
            ThreatLevel.CRITICAL: 4,
        }
        return ranks.get(severity, 0)


# =============================================================================
# Heuristic Classifier
# =============================================================================

class HeuristicClassifier:
    """Rule-based heuristic analysis for edge cases.

    Analyzes content characteristics:
    - Entropy (detect obfuscation)
    - Token frequency (detect unusual patterns)
    - Length anomalies

    Runs after regex classifier for content that passes initial screening.

    Example:
        classifier = HeuristicClassifier()
        verdict = classifier.classify("SGVsbG8gV29ybGQ=", {"content_type": "command"})
    """

    __slots__ = (
        "_entropy_threshold",
        "_min_length_for_entropy",
        "_max_line_length",
    )

    def __init__(
        self,
        *,
        entropy_threshold: float = 4.5,
        min_length_for_entropy: int = 50,
        max_line_length: int = 500,
    ) -> None:
        """Initialize heuristic classifier.

        Args:
            entropy_threshold: Shannon entropy threshold for obfuscation detection
            min_length_for_entropy: Minimum content length to check entropy
            max_line_length: Maximum expected line length
        """
        self._entropy_threshold = entropy_threshold
        self._min_length_for_entropy = min_length_for_entropy
        self._max_line_length = max_line_length

    def classify(
        self,
        content: str,
        context: dict[str, str] | None = None,
    ) -> SecurityVerdict:
        """Classify content using heuristics.

        Args:
            content: Content to classify
            context: Optional context (content_type, etc.)

        Returns:
            SecurityVerdict with classification result
        """
        context = context or {}
        issues: list[str] = []

        # Check entropy (potential obfuscation)
        if len(content) >= self._min_length_for_entropy:
            entropy = self._calculate_entropy(content)
            if entropy > self._entropy_threshold:
                issues.append(f"High entropy ({entropy:.2f}) - possible obfuscation")

        # Check for very long lines (potential encoded payloads)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if len(line) > self._max_line_length:
                issues.append(f"Very long line {i+1} ({len(line)} chars)")
                break

        # Check for high ratio of special characters
        if content:
            special_ratio = sum(1 for c in content if not c.isalnum() and not c.isspace()) / len(content)
            if special_ratio > 0.4:
                issues.append(f"High special character ratio ({special_ratio:.1%})")

        # Check for base64-like content
        if self._looks_like_base64(content):
            issues.append("Content appears to be encoded (base64-like)")

        # Return verdict
        if issues:
            # Medium severity for heuristic findings (uncertain)
            return SecurityVerdict(
                allowed=True,  # Allow but flag for review
                score=Decimal("0.4"),
                threat_level=ThreatLevel.LOW,
                classifier=ClassifierType.HEURISTIC,
                reason="; ".join(issues),
            )

        return SecurityVerdict.safe(classifier=ClassifierType.HEURISTIC)

    def _calculate_entropy(self, text: str) -> float:
        """Calculate Shannon entropy of text."""
        if not text:
            return 0.0

        counter = Counter(text)
        length = len(text)
        entropy = 0.0

        for count in counter.values():
            if count > 0:
                freq = count / length
                entropy -= freq * math.log2(freq)

        return entropy

    def _looks_like_base64(self, text: str) -> bool:
        """Check if text looks like base64 encoded content."""
        # Base64 pattern: mostly alphanumeric + /=, length divisible by 4
        clean = text.strip()
        if len(clean) < 20:
            return False

        base64_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        char_ratio = sum(1 for c in clean if c in base64_chars) / len(clean)

        return char_ratio > 0.95 and len(clean) % 4 == 0


# =============================================================================
# LLM Classifier (Placeholder)
# =============================================================================

class LLMClassifier:
    """LLM-based classification (placeholder for future implementation).

    Would use an LLM to analyze content for sophisticated threats
    that regex and heuristics might miss.

    Not implemented yet - requires LLM integration.
    """

    __slots__ = ("_enabled",)

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    async def classify(self, content: str) -> SecurityVerdict:
        """Classify content using LLM (not implemented).

        Args:
            content: Content to classify

        Returns:
            Safe verdict (LLM not implemented)
        """
        # Placeholder - always returns safe
        return SecurityVerdict.safe(classifier=ClassifierType.LLM)

    @property
    def enabled(self) -> bool:
        """Whether LLM classification is enabled."""
        return self._enabled
