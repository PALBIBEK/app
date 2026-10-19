"""Security screener — multi-layer content screening orchestrator.

Orchestrates content screening through multiple layers:
1. Regex: Fast pattern matching (always runs)
2. Heuristic: Rule-based analysis (if regex passes)
3. LLM: AI-based analysis (optional, expensive)

Features:
- Shadow mode: Log but don't block
- Quarantine: Store blocked content for review
- Configurable thresholds

Reference: QM's security-screener.ts pattern.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from supervisor.policy.security.classifiers import (
    HeuristicClassifier,
    LLMClassifier,
    RegexClassifier,
)
from supervisor.policy.security.types import (
    DEFAULT_THREAT_THRESHOLD,
    ContentType,
    SecurityVerdict,
    ThreatLevel,
)

if TYPE_CHECKING:
    from supervisor.policy.security.quarantine import QuarantineStore


__all__ = [
    "DEFAULT_THREAT_THRESHOLD",
    "SecurityScreener",
]


_log = logging.getLogger(__name__)


class SecurityScreener:
    """Multi-layer security screening orchestrator.

    Screens content through multiple classification layers:
    1. Regex (fast, always runs)
    2. Heuristic (if regex passes)
    3. LLM (optional, if enabled)

    Example:
        screener = SecurityScreener()

        # Screen a command
        verdict = await screener.screen("rm -rf /", content_type=ContentType.COMMAND)
        if not verdict.allowed:
            raise SecurityError(verdict.reason)

        # Screen with shadow mode (log but don't block)
        screener_shadow = SecurityScreener(shadow_mode=True)
        verdict = await screener_shadow.screen(content)
        # verdict.allowed will be True but verdict.shadow_mode will be True
    """

    __slots__ = (
        "_enable_heuristic",
        "_enable_llm",
        "_heuristic",
        "_llm",
        "_quarantine",
        "_regex",
        "_shadow_mode",
        "_threat_threshold",
    )

    def __init__(
        self,
        *,
        quarantine_store: QuarantineStore | None = None,
        shadow_mode: bool = False,
        threat_threshold: Decimal = DEFAULT_THREAT_THRESHOLD,
        enable_heuristic: bool = True,
        enable_llm: bool = False,
    ) -> None:
        """Initialize security screener.

        Args:
            quarantine_store: Store for blocked content (optional)
            shadow_mode: If True, log but don't block
            threat_threshold: Score threshold for blocking (0.0-1.0)
            enable_heuristic: Whether to run heuristic classifier
            enable_llm: Whether to run LLM classifier
        """
        self._regex = RegexClassifier()
        self._heuristic = HeuristicClassifier() if enable_heuristic else None
        self._llm = LLMClassifier(enabled=enable_llm) if enable_llm else None
        self._quarantine = quarantine_store
        self._shadow_mode = shadow_mode
        self._threat_threshold = threat_threshold
        self._enable_heuristic = enable_heuristic
        self._enable_llm = enable_llm

    async def screen(
        self,
        content: str,
        *,
        content_type: ContentType = ContentType.COMMAND,
        session_id: str | None = None,
        principal_id: str | None = None,
    ) -> SecurityVerdict:
        """Screen content through all classification layers.

        Order:
        1. Regex (always)
        2. Heuristic (if enabled and regex passes)
        3. LLM (if enabled and previous pass)

        Args:
            content: Content to screen
            content_type: Type of content
            session_id: Session for quarantine tracking
            principal_id: User/principal for quarantine tracking

        Returns:
            SecurityVerdict with final decision
        """
        # Layer 1: Regex (fast, always runs)
        verdict = self._regex.classify(content)

        if not verdict.allowed:
            return await self._handle_blocked(
                verdict,
                content,
                content_type,
                session_id,
                principal_id,
            )

        # Layer 2: Heuristic (if enabled)
        if self._heuristic and verdict.score < self._threat_threshold:
            heuristic_verdict = self._heuristic.classify(
                content,
                {"content_type": content_type.value},
            )

            # Merge verdicts (take worse)
            if heuristic_verdict.score > verdict.score:
                verdict = SecurityVerdict(
                    allowed=verdict.allowed and heuristic_verdict.allowed,
                    score=max(verdict.score, heuristic_verdict.score),
                    threat_level=self._max_threat_level(
                        verdict.threat_level,
                        heuristic_verdict.threat_level,
                    ),
                    classifier=heuristic_verdict.classifier,
                    reason=heuristic_verdict.reason or verdict.reason,
                    patterns_matched=verdict.patterns_matched + heuristic_verdict.patterns_matched,
                )

        # Layer 3: LLM (if enabled and score still uncertain)
        if self._llm and self._llm.enabled:
            if Decimal("0.3") <= verdict.score <= Decimal("0.7"):
                llm_verdict = await self._llm.classify(content)

                # Merge with LLM verdict
                if llm_verdict.score > verdict.score:
                    verdict = SecurityVerdict(
                        allowed=verdict.allowed and llm_verdict.allowed,
                        score=max(verdict.score, llm_verdict.score),
                        threat_level=self._max_threat_level(
                            verdict.threat_level,
                            llm_verdict.threat_level,
                        ),
                        classifier=llm_verdict.classifier,
                        reason=llm_verdict.reason or verdict.reason,
                        patterns_matched=verdict.patterns_matched,
                    )

        # Check if should block based on threshold
        if verdict.score >= self._threat_threshold:
            verdict = SecurityVerdict(
                allowed=False,
                score=verdict.score,
                threat_level=verdict.threat_level,
                classifier=verdict.classifier,
                reason=verdict.reason or "Score exceeds threshold",
                patterns_matched=verdict.patterns_matched,
            )
            return await self._handle_blocked(
                verdict,
                content,
                content_type,
                session_id,
                principal_id,
            )

        return verdict

    async def screen_command(
        self,
        command: str,
        session_id: str,
        *,
        principal_id: str | None = None,
    ) -> SecurityVerdict:
        """Screen a shell command.

        Convenience method for command screening.

        Args:
            command: Shell command to screen
            session_id: Session ID
            principal_id: Optional principal ID

        Returns:
            SecurityVerdict
        """
        return await self.screen(
            command,
            content_type=ContentType.COMMAND,
            session_id=session_id,
            principal_id=principal_id,
        )

    async def screen_file_content(
        self,
        content: str,
        path: str,
        session_id: str,
        *,
        principal_id: str | None = None,
    ) -> SecurityVerdict:
        """Screen file content.

        Args:
            content: File content
            path: File path (for context)
            session_id: Session ID
            principal_id: Optional principal ID

        Returns:
            SecurityVerdict
        """
        return await self.screen(
            content,
            content_type=ContentType.FILE_CONTENT,
            session_id=session_id,
            principal_id=principal_id,
        )

    async def screen_code(
        self,
        code: str,
        language: str,
        session_id: str,
        *,
        principal_id: str | None = None,
    ) -> SecurityVerdict:
        """Screen source code.

        Args:
            code: Source code
            language: Programming language
            session_id: Session ID
            principal_id: Optional principal ID

        Returns:
            SecurityVerdict
        """
        return await self.screen(
            code,
            content_type=ContentType.CODE,
            session_id=session_id,
            principal_id=principal_id,
        )

    async def _handle_blocked(
        self,
        verdict: SecurityVerdict,
        content: str,
        content_type: ContentType,
        session_id: str | None,
        principal_id: str | None,
    ) -> SecurityVerdict:
        """Handle blocked content - quarantine and possibly shadow mode.

        Args:
            verdict: Blocking verdict
            content: Blocked content
            content_type: Type of content
            session_id: Session ID
            principal_id: Principal ID

        Returns:
            Final verdict (may be modified for shadow mode)
        """
        quarantine_id: str | None = None

        # Quarantine if store available
        if self._quarantine and session_id:
            try:
                quarantine_id = await self._quarantine.quarantine(
                    content,
                    verdict,
                    session_id,
                    content_type=content_type,
                    principal_id=principal_id,
                )
            except Exception:
                _log.exception("Failed to quarantine content")

        # Log the block
        _log.warning(
            f"Security block: {verdict.reason} "
            f"(score={verdict.score}, level={verdict.threat_level}, "
            f"classifier={verdict.classifier}, session={session_id})"
        )

        # If shadow mode, allow but mark
        if self._shadow_mode:
            return SecurityVerdict(
                allowed=True,
                score=verdict.score,
                threat_level=verdict.threat_level,
                classifier=verdict.classifier,
                reason=verdict.reason,
                patterns_matched=verdict.patterns_matched,
                quarantine_id=quarantine_id,
                shadow_mode=True,
            )

        # Return blocked verdict with quarantine ID
        return SecurityVerdict(
            allowed=False,
            score=verdict.score,
            threat_level=verdict.threat_level,
            classifier=verdict.classifier,
            reason=verdict.reason,
            patterns_matched=verdict.patterns_matched,
            quarantine_id=quarantine_id,
        )

    def _max_threat_level(
        self,
        a: ThreatLevel,
        b: ThreatLevel,
    ) -> ThreatLevel:
        """Get the more severe threat level."""
        ranks = {
            ThreatLevel.SAFE: 0,
            ThreatLevel.LOW: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.HIGH: 3,
            ThreatLevel.CRITICAL: 4,
        }
        return a if ranks.get(a, 0) >= ranks.get(b, 0) else b

    # Configuration properties
    @property
    def shadow_mode(self) -> bool:
        """Whether screener is in shadow mode."""
        return self._shadow_mode

    @property
    def threat_threshold(self) -> Decimal:
        """Score threshold for blocking."""
        return self._threat_threshold

    @property
    def heuristic_enabled(self) -> bool:
        """Whether heuristic classifier is enabled."""
        return self._enable_heuristic

    @property
    def llm_enabled(self) -> bool:
        """Whether LLM classifier is enabled."""
        return self._enable_llm
