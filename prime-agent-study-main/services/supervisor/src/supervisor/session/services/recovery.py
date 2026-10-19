"""Session recovery decision engine.

Determines whether and how a session can be resumed after restart.
Does not perform actions - only makes decisions.

Recovery states:
- WARM: Session still active in memory, no recovery needed
- COLD: Safe to resume from journal replay
- NEEDS_REVIEW: Human review required (unpaired tools/compactions)
- FAILED: Unrecoverable tool failure per recovery policy
- UNRECOVERABLE: Sandbox state lost, cannot resume

Unpaired tool outcomes are classified using the session's **pinned** harness
recovery policy - never the current/latest manifest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from supervisor.agent.events import ToolStart
from supervisor.harness import (
    HarnessCatalog,
    HarnessManifest,
    RecoveryAction,
    manifest_from_dict,
)
from supervisor.session.core.enums import ResumeState
from supervisor.session.core.protocols import SessionRowView
from supervisor.session.core.types import ResumeDecision

if TYPE_CHECKING:
    from supervisor.journal.reader import JournalReader
    from supervisor.session.storage.registry import SessionRegistry

__all__ = [
    "Recovery",
    "decide_unpaired",
    "pinned_manifest",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Recovery Helpers
# =============================================================================


def decide_unpaired(
    unpaired: list[ToolStart],
    *,
    policy: Any,
) -> tuple[ResumeState, tuple[RecoveryAction, ...]]:
    """Map unpaired tools through recovery policy to aggregate state.

    Args:
        unpaired: List of ToolStart events without matching ToolEnd
        policy: Recovery policy from harness manifest

    Returns:
        Tuple of (aggregate state, actions applied per tool)
    """
    if not unpaired:
        return ResumeState.COLD, ()

    actions: list[RecoveryAction] = []
    for tool in unpaired:
        actions.append(policy.action_for(tool.tool_name))

    # Fail takes precedence over needs_review
    if any(a == RecoveryAction.FAIL for a in actions):
        logger.info(
            "recovery failed due to unrecoverable tool",
            extra={"tool_count": len(unpaired)},
        )
        return ResumeState.FAILED, tuple(actions)

    if any(a == RecoveryAction.NEEDS_REVIEW for a in actions):
        logger.info(
            "recovery needs review",
            extra={"tool_count": len(unpaired)},
        )
        return ResumeState.NEEDS_REVIEW, tuple(actions)

    return ResumeState.COLD, tuple(actions)


def pinned_manifest(
    session_row: SessionRowView,
    catalog: HarnessCatalog,
) -> HarnessManifest:
    """Retrieve the pinned harness manifest for a session.

    Uses the stored manifest snapshot if available, otherwise
    resolves from catalog. This ensures recovery uses the same
    manifest the session was created with.

    Args:
        session_row: Session database row
        catalog: Harness catalog for fallback resolution

    Returns:
        HarnessManifest for the session
    """
    stored = session_row.manifest
    if stored:
        return manifest_from_dict(dict(stored))
    return catalog.resolve_sync(session_row.harness_version, for_create=False)


# =============================================================================
# Recovery Engine
# =============================================================================


class Recovery:
    """Session recovery decision engine.

    Determines the resumability state of a session without
    performing any recovery actions. The actual recovery
    is performed by SessionManager based on the decision.

    Example:
        recovery = Recovery(
            registry=registry,
            reader=reader,
            catalog=catalog,
        )

        decision = await recovery.decide(
            session_id="sess-123",
            session_row=row,
            sandbox_resumable=True,
        )

        if decision.state == ResumeState.COLD:
            # Safe to resume from journal
            ...
        elif decision.state == ResumeState.NEEDS_REVIEW:
            # Requires human intervention
            ...
    """

    def __init__(
        self,
        *,
        registry: SessionRegistry,
        reader: JournalReader,
        catalog: HarnessCatalog,
        sandbox_provider: Any | None = None,
    ) -> None:
        """Initialize recovery engine.

        Args:
            registry: Session registry for warm session detection
            reader: Journal reader for unpaired event detection
            catalog: Harness catalog for manifest resolution
            sandbox_provider: Optional sandbox provider for resumability check
        """
        self._registry = registry
        self._reader = reader
        self._catalog = catalog
        self._provider = sandbox_provider

    async def decide(
        self,
        session_id: str,
        *,
        session_row: SessionRowView,
        sandbox_resumable: bool = True,
    ) -> ResumeDecision:
        """Determine how a session should be resumed.

        Decision Logic:
            1. If session in registry → WARM (already active)
            2. Check unpaired tools → apply recovery policy
            3. Check unpaired compactions → NEEDS_REVIEW
            4. Check sandbox resumability → COLD or UNRECOVERABLE

        Args:
            session_id: The session to evaluate
            session_row: Database row with session state
            sandbox_resumable: Whether sandbox can be resumed

        Returns:
            ResumeDecision with state and metadata
        """
        # Check if session is already warm in memory
        if self._registry.get(session_id) is not None:
            logger.debug(
                "session is warm",
                extra={"session_id": session_id},
            )
            return ResumeDecision(state=ResumeState.WARM)

        # Always use pinned harness snapshot - never "current" catalog version
        harness = pinned_manifest(session_row, self._catalog)

        # Check for unpaired tool starts
        unpaired = await self._reader.unpaired_tool_starts(session_id)
        if unpaired:
            state, actions = decide_unpaired(unpaired, policy=harness.recovery)
            logger.info(
                "recovery decision for unpaired tools",
                extra={
                    "session_id": session_id,
                    "state": state.value,
                    "tool_count": len(unpaired),
                },
            )
            return ResumeDecision(
                state=state,
                harness=harness,
                unpaired_tool_call_ids=tuple(u.tool_call_id for u in unpaired),
                applied_actions=actions,
            )

        # Check for unpaired compaction starts
        unpaired_cmp = await self._reader.unpaired_compaction_starts(session_id)
        if unpaired_cmp:
            logger.info(
                "recovery needs review for unpaired compactions",
                extra={
                    "session_id": session_id,
                    "compaction_count": len(unpaired_cmp),
                },
            )
            return ResumeDecision(
                state=ResumeState.NEEDS_REVIEW,
                harness=harness,
                unpaired_compaction_ids=tuple(c.compaction_id for c in unpaired_cmp),
            )

        # No sandbox required - safe to resume cold
        if not harness.requires_sandbox:
            return ResumeDecision(state=ResumeState.COLD, harness=harness)

        # Check sandbox resumability
        if sandbox_resumable:
            return ResumeDecision(state=ResumeState.COLD, harness=harness)

        logger.warning(
            "session unrecoverable - sandbox not resumable",
            extra={"session_id": session_id},
        )
        return ResumeDecision(state=ResumeState.UNRECOVERABLE, harness=harness)
