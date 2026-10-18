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

from supervisor.policy.security.types import (
    # Enums
    ThreatLevel,
    ClassifierType,
    ContentType,
    # Dataclasses
    SecurityVerdict,
    QuarantinedContent,
    PatternMatch,
    # Constants
    DEFAULT_THREAT_THRESHOLD,
    DEFAULT_QUARANTINE_RETENTION_MS,
)

from supervisor.policy.security.classifiers import (
    RegexClassifier,
    HeuristicClassifier,
    DANGEROUS_PATTERNS,
    SUSPICIOUS_PATTERNS,
)

from supervisor.policy.security.screener import SecurityScreener

from supervisor.policy.security.quarantine import (
    QuarantineStore,
    InMemoryQuarantineStore,
    DEFAULT_MAX_QUARANTINE_SIZE,
)


__all__ = [
    # Main screener
    "SecurityScreener",
    # Classifiers
    "RegexClassifier",
    "HeuristicClassifier",
    # Quarantine
    "QuarantineStore",
    "InMemoryQuarantineStore",
    # Types - Enums
    "ThreatLevel",
    "ClassifierType",
    "ContentType",
    # Types - Dataclasses
    "SecurityVerdict",
    "QuarantinedContent",
    "PatternMatch",
    # Constants
    "DEFAULT_THREAT_THRESHOLD",
    "DEFAULT_QUARANTINE_RETENTION_MS",
    "DEFAULT_MAX_QUARANTINE_SIZE",
    # Pattern collections
    "DANGEROUS_PATTERNS",
    "SUSPICIOUS_PATTERNS",
]
