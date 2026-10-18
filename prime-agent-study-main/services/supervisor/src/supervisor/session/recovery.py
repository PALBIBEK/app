"""Decide warm / cold / needs_review / failed / unrecoverable. Does not act.

Unpaired tool outcomes are classified using the session's **pinned** harness
recovery policy — never the current/latest manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from supervisor.agent.events import ToolStart
from supervisor.harness import (
    HarnessCatalog,
    HarnessManifest,
    RecoveryAction,
    manifest_from_dict,
)
from supervisor.journal.reader import JournalReader
from supervisor.session.registry import SessionRegistry


class ResumeState(StrEnum):
    WARM = "warm"
    COLD = "cold"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    UNRECOVERABLE = "unrecoverable"


class SessionRowView(Protocol):
    harness_version: str
    runtime: str
    status: str
    manifest: Any


@dataclass(frozen=True)
class ResumeDecision:
    state: ResumeState
    harness: HarnessManifest | None = None
    unpaired_tool_call_ids: tuple[str, ...] = ()
    unpaired_compaction_ids: tuple[str, ...] = ()
    applied_actions: tuple[RecoveryAction, ...] = ()


def decide_unpaired(
    unpaired: list[ToolStart],
    *,
    policy,
) -> tuple[ResumeState, tuple[RecoveryAction, ...]]:
    """Map unpaired tools through recovery policy → aggregate state."""
    if not unpaired:
        return ResumeState.COLD, ()

    actions: list[RecoveryAction] = []
    for tool in unpaired:
        actions.append(policy.action_for(tool.tool_name))

    if any(a == RecoveryAction.FAIL for a in actions):
        return ResumeState.FAILED, tuple(actions)
    if any(a == RecoveryAction.NEEDS_REVIEW for a in actions):
        return ResumeState.NEEDS_REVIEW, tuple(actions)
    return ResumeState.COLD, tuple(actions)


def pinned_manifest(session_row: SessionRowView, catalog: HarnessCatalog) -> HarnessManifest:
    stored = getattr(session_row, "manifest", None)
    if stored:
        return manifest_from_dict(dict(stored))
    return catalog.resolve_sync(session_row.harness_version, for_create=False)


class Recovery:
    def __init__(
        self,
        *,
        registry: SessionRegistry,
        reader: JournalReader,
        catalog: HarnessCatalog,
        sandbox_provider: Any | None = None,
    ) -> None:
        self._registry = registry
        self._reader = reader
        self._catalog = catalog
        self._provider = sandbox_provider

    async def decide(
        self,
        session_id: str,
        *,
        session_row: SessionRowView,
        sandbox_resumable: bool | None = None,
    ) -> ResumeDecision:
        if self._registry.get(session_id) is not None:
            return ResumeDecision(state=ResumeState.WARM)

        # Always the pinned harness snapshot — never "current" file/catalog edit.
        harness = pinned_manifest(session_row, self._catalog)

        unpaired = await self._reader.unpaired_tool_starts(session_id)
        if unpaired:
            state, actions = decide_unpaired(unpaired, policy=harness.recovery)
            return ResumeDecision(
                state=state,
                harness=harness,
                unpaired_tool_call_ids=tuple(u.tool_call_id for u in unpaired),
                applied_actions=actions,
            )

        unpaired_cmp = await self._reader.unpaired_compaction_starts(session_id)
        if unpaired_cmp:
            return ResumeDecision(
                state=ResumeState.NEEDS_REVIEW,
                harness=harness,
                unpaired_compaction_ids=tuple(c.compaction_id for c in unpaired_cmp),
            )

        if not harness.requires_sandbox:
            return ResumeDecision(state=ResumeState.COLD, harness=harness)

        if sandbox_resumable is None:
            sandbox_resumable = True
        if sandbox_resumable:
            return ResumeDecision(state=ResumeState.COLD, harness=harness)
        return ResumeDecision(state=ResumeState.UNRECOVERABLE, harness=harness)
