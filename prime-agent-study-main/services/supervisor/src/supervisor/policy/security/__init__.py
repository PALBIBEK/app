"""Security screening subsystem — multi-layer content analysis.

Components:
- SecurityScreener: Main orchestrator for content screening
- RegexClassifier: Fast pattern-based classification
- HeuristicClassifier: Rule-based analysis for edge cases
- QuarantineStore: Blocked content storage for review

Types:
- ThreatLevel: Severity classification
- ClassifierType: Classifier identification
- SecurityVerdict: Screening result
- QuarantinedContent: Blocked content metadata

Example:
    from supervisor.policy.security import SecurityScreener, ContentType

    screener = SecurityScreener()

    # Screen a command
    verdict = await screener.screen_command("rm -rf /", session_id="sess_123")
    if not verdict.allowed:
        raise SecurityError(verdict.reason)

    # With shadow mode (log but don't block)
    screener = SecurityScreener(shadow_mode=True)

Reference: QM's security-screener.ts pattern.
"""

from supervisor.policy.security.classifiers import DANGEROUS_PATTERNS, SUSPICIOUS_PATTERNS
from supervisor.policy.security.quarantine import DEFAULT_MAX_QUARANTINE_SIZE
from supervisor.policy.security.types import (
    DEFAULT_QUARANTINE_RETENTION_MS,
    # Constants
    DEFAULT_THREAT_THRESHOLD,
    ClassifierType,
    ContentType,
    PatternMatch,
    QuarantinedContent,
    # Dataclasses
    SecurityVerdict,
    # Enums
    ThreatLevel,
)

__all__ = [
    "DANGEROUS_PATTERNS",
    "DEFAULT_MAX_QUARANTINE_SIZE",
    "DEFAULT_QUARANTINE_RETENTION_MS",
    "DEFAULT_THREAT_THRESHOLD",
    "SUSPICIOUS_PATTERNS",
    "ClassifierType",
    "ContentType",
    "PatternMatch",
    "QuarantinedContent",
    "SecurityVerdict",
    "ThreatLevel",
]
