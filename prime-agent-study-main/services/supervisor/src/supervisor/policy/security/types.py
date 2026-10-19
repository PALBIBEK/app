"""Security screening types — enums and dataclasses.

Types for multi-layer security screening:
- ThreatLevel: Severity classification
- ClassifierType: Which classifier made the decision
- SecurityVerdict: Screening result
- QuarantinedContent: Blocked content metadata

Reference: QM's security-screener.ts pattern.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, auto
from typing import Any, Final


__all__ = [
    # Enums
    "ThreatLevel",
    "ClassifierType",
    "ContentType",
    # Dataclasses
    "SecurityVerdict",
    "QuarantinedContent",
    "PatternMatch",
    # Constants
    "DEFAULT_THREAT_THRESHOLD",
    "DEFAULT_QUARANTINE_RETENTION_MS",
]


# =============================================================================
# Constants
# =============================================================================

# Default score threshold for blocking (0.7 = 70% confidence)
DEFAULT_THREAT_THRESHOLD: Final[Decimal] = Decimal("0.7")

# Default quarantine retention (7 days)
DEFAULT_QUARANTINE_RETENTION_MS: Final[int] = 7 * 24 * 60 * 60 * 1000


# =============================================================================
# Enums
# =============================================================================

class ThreatLevel(StrEnum):
    """Severity level of detected threat."""
    SAFE = "safe"           # No threat detected
    LOW = "low"             # Minor concern, log only
    MEDIUM = "medium"       # Concerning, may require review
    HIGH = "high"           # Dangerous, should block
    CRITICAL = "critical"   # Severe threat, must block


class ClassifierType(StrEnum):
    """Type of classifier that made the decision."""
    REGEX = "regex"           # Fast pattern matching
    HEURISTIC = "heuristic"   # Rule-based analysis
    LLM = "llm"               # LLM-based classification


class ContentType(StrEnum):
    """Type of content being screened."""
    COMMAND = "command"         # Shell command
    CODE = "code"               # Source code
    FILE_CONTENT = "file"       # File contents
    API_RESPONSE = "api"        # External API response
    USER_INPUT = "user"         # User-provided input
    TOOL_ARGS = "tool_args"     # Tool arguments


# =============================================================================
# Dataclasses
# =============================================================================

@dataclass(frozen=True, slots=True)
class PatternMatch:
    """Details of a pattern that matched."""
    pattern: str              # Pattern that matched
    category: str             # Category (e.g., "data_exfiltration")
    severity: ThreatLevel     # Severity of match
    matched_text: str         # Text that matched (truncated)
    start_pos: int           # Start position in content
    end_pos: int             # End position in content


@dataclass(frozen=True, slots=True)
class SecurityVerdict:
    """Result of security screening.

    Score is 0.0-1.0 where:
    - 0.0 = completely safe
    - 0.5 = uncertain
    - 1.0 = definitely dangerous
    """
    allowed: bool                   # Whether to allow the content
    score: Decimal                  # Threat score (0.0 = safe, 1.0 = danger)
    threat_level: ThreatLevel       # Severity classification
    classifier: ClassifierType      # Which classifier made decision
    reason: str | None = None       # Human-readable reason
    patterns_matched: tuple[PatternMatch, ...] = field(default_factory=tuple)
    quarantine_id: str | None = None  # If quarantined, the ID
    shadow_mode: bool = False       # If True, logged but not blocked

    @classmethod
    def safe(
        cls,
        *,
        classifier: ClassifierType = ClassifierType.REGEX,
    ) -> SecurityVerdict:
        """Create a safe verdict."""
        return cls(
            allowed=True,
            score=Decimal("0.0"),
            threat_level=ThreatLevel.SAFE,
            classifier=classifier,
        )

    @classmethod
    def blocked(
        cls,
        reason: str,
        *,
        score: Decimal = Decimal("1.0"),
        threat_level: ThreatLevel = ThreatLevel.HIGH,
        classifier: ClassifierType = ClassifierType.REGEX,
        patterns: tuple[PatternMatch, ...] = (),
        quarantine_id: str | None = None,
    ) -> SecurityVerdict:
        """Create a blocked verdict."""
        return cls(
            allowed=False,
            score=score,
            threat_level=threat_level,
            classifier=classifier,
            reason=reason,
            patterns_matched=patterns,
            quarantine_id=quarantine_id,
        )

    @property
    def is_blocked(self) -> bool:
        """Whether content was blocked."""
        return not self.allowed


@dataclass(frozen=True, slots=True)
class QuarantinedContent:
    """Metadata for blocked content stored for review.

    Note: Raw content is hashed, not stored for security.
    """
    quarantine_id: str              # Unique ID
    content_hash: str               # Hash of content (not raw)
    content_preview: str            # First 100 chars (redacted)
    content_type: ContentType       # Type of content
    verdict: SecurityVerdict        # Why it was blocked
    session_id: str                 # Session that triggered
    principal_id: str | None        # User/principal if known
    timestamp_ms: int               # When quarantined
    reviewed: bool = False          # Whether reviewed by admin
    review_action: str | None = None  # Action taken (allow/block/delete)
    review_by: str | None = None    # Who reviewed
    review_at_ms: int | None = None # When reviewed

    @classmethod
    def create(
        cls,
        quarantine_id: str,
        content_hash: str,
        content_preview: str,
        content_type: ContentType,
        verdict: SecurityVerdict,
        session_id: str,
        *,
        principal_id: str | None = None,
    ) -> QuarantinedContent:
        """Create new quarantined content record."""
        return cls(
            quarantine_id=quarantine_id,
            content_hash=content_hash,
            content_preview=content_preview,
            content_type=content_type,
            verdict=verdict,
            session_id=session_id,
            principal_id=principal_id,
            timestamp_ms=int(time.time() * 1000),
        )


def _truncate_preview(content: str, max_len: int = 100) -> str:
    """Truncate content for preview, redacting sensitive parts."""
    if len(content) <= max_len:
        return content
    return content[:max_len] + "..."
