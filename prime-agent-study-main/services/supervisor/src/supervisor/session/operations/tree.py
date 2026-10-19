"""Session child tree rebuilt from journal Subagent*/Job* events.

Provides harness-neutral reconstruction of session hierarchy from
journal events, enabling crash recovery and session tree visualization.

The tree includes:
- Child sessions (spawned, forked, continuable)
- Background jobs
- Unpaired run tracking for recovery
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from supervisor.agent.events import (
    JobEnd,
    JobStart,
    SubagentEnd,
    SubagentStart,
)
from supervisor.session.core.enums import ChildActivity, JobActivity

if TYPE_CHECKING:
    from supervisor.agent.events import Envelope

__all__ = [
    "ChildRow",
    "JobRow",
    "SessionTree",
    "build_session_tree",
    "unpaired_subagent_starts",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Tree Row Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class ChildRow:
    """One child node visible to supervisor / UI (scale contracts ChildRow).

    Represents a spawned subagent, forked session, or continuable child.
    """

    id: str
    mode: str
    provider: str
    parent_id: str
    run_id: str
    label: str | None = None
    activity: ChildActivity = ChildActivity.RUNNING
    depth: int | None = None


@dataclass(frozen=True, slots=True)
class JobRow:
    """Background job execution record.

    Tracks job lifecycle from start to completion.
    """

    job_id: str
    kind: str
    activity: JobActivity = JobActivity.RUNNING
    stop_reason: str | None = None
    output: str | None = None


# =============================================================================
# Session Tree
# =============================================================================


@dataclass
class SessionTree:
    """In-memory tree derived from a parent session's journal envelopes.

    Reconstructed from SubagentStart/End and JobStart/End events
    to provide session hierarchy for UI, recovery, and management.
    """

    parent_session_id: str
    children: dict[str, ChildRow] = field(default_factory=dict)
    jobs: dict[str, JobRow] = field(default_factory=dict)
    unpaired_run_ids: frozenset[str] = field(default_factory=frozenset)
    open_job_ids: frozenset[str] = field(default_factory=frozenset)

    def list_children(
        self,
        *,
        scope: Literal["direct", "descendants"] = "direct",
    ) -> list[ChildRow]:
        """List child sessions.

        Args:
            scope: "direct" for immediate children, "descendants" for all
                   (descendants reserved for future, same as direct for now)

        Returns:
            List of ChildRow instances

        Raises:
            ValueError: If scope is not recognized
        """
        if scope not in ("direct", "descendants"):
            raise ValueError(f"unknown scope: {scope!r}")
        return [
            c
            for c in self.children.values()
            if c.parent_id == self.parent_session_id
        ]

    def list_jobs(self) -> list[JobRow]:
        """List all background jobs.

        Returns:
            List of JobRow instances
        """
        return list(self.jobs.values())

    def has_unpaired_runs(self) -> bool:
        """Check if there are unpaired subagent runs.

        Returns:
            True if any runs started but not ended
        """
        return len(self.unpaired_run_ids) > 0

    def has_open_jobs(self) -> bool:
        """Check if there are open jobs.

        Returns:
            True if any jobs started but not ended
        """
        return len(self.open_job_ids) > 0


# =============================================================================
# Tree Building
# =============================================================================


def build_session_tree(
    parent_session_id: str,
    envelopes: list[Envelope],
) -> SessionTree:
    """Fold SubagentStart/End and JobStart/End into a SessionTree.

    Processes journal envelopes in order to reconstruct the session
    hierarchy, tracking open/closed runs and jobs.

    Args:
        parent_session_id: The parent session identifier
        envelopes: List of journal envelopes to process

    Returns:
        SessionTree with children, jobs, and tracking sets
    """
    children: dict[str, ChildRow] = {}
    jobs: dict[str, JobRow] = {}
    open_runs: dict[str, str] = {}  # run_id → child_session_id
    open_jobs: set[str] = set()

    for env in envelopes:
        p = env.payload

        if isinstance(p, SubagentStart):
            children[p.child_session_id] = ChildRow(
                id=p.child_session_id,
                mode=p.mode,
                provider=p.provider,
                parent_id=p.parent_session_id,
                run_id=p.run_id,
                label=p.label,
                activity=ChildActivity.RUNNING,
            )
            open_runs[p.run_id] = p.child_session_id

        elif isinstance(p, SubagentEnd):
            cid = open_runs.pop(p.run_id, None)
            if cid is None:
                # End without start — skip
                continue
            prev = children.get(cid)
            if prev is not None:
                children[cid] = ChildRow(
                    id=prev.id,
                    mode=prev.mode,
                    provider=prev.provider,
                    parent_id=prev.parent_id,
                    run_id=prev.run_id,
                    label=prev.label,
                    activity=ChildActivity.DONE,
                    depth=prev.depth,
                )

        elif isinstance(p, JobStart):
            jobs[p.job_id] = JobRow(
                job_id=p.job_id,
                kind=p.kind,
                activity=JobActivity.RUNNING,
            )
            open_jobs.add(p.job_id)

        elif isinstance(p, JobEnd):
            open_jobs.discard(p.job_id)
            prev_j = jobs.get(p.job_id)
            jobs[p.job_id] = JobRow(
                job_id=p.job_id,
                kind=prev_j.kind if prev_j else "unknown",
                activity=JobActivity.DONE,
                stop_reason=p.stop_reason,
                output=p.output,
            )

    return SessionTree(
        parent_session_id=parent_session_id,
        children=children,
        jobs=jobs,
        unpaired_run_ids=frozenset(open_runs),
        open_job_ids=frozenset(open_jobs),
    )


def unpaired_subagent_starts(envelopes: list[Envelope]) -> list[SubagentStart]:
    """Find SubagentStart payloads whose run_id has no matching SubagentEnd.

    Useful for recovery to identify incomplete subagent executions.

    Args:
        envelopes: List of journal envelopes to process

    Returns:
        List of unpaired SubagentStart events
    """
    ended: set[str] = set()
    starts: dict[str, SubagentStart] = {}

    for env in envelopes:
        if isinstance(env.payload, SubagentStart):
            starts[env.payload.run_id] = env.payload
        elif isinstance(env.payload, SubagentEnd):
            ended.add(env.payload.run_id)

    return [starts[rid] for rid in starts if rid not in ended]
