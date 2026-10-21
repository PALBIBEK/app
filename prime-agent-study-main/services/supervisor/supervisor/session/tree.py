"""Session child tree rebuilt from journal Subagent*/Job* events (harness-neutral)."""

from __future__ import annotations

from dataclasses import dataclass

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
    JobEnd,
    JobStart,
    SubagentEnd,
    SubagentStart,
)


@dataclass(frozen=True)
class ChildRow:
    """One child node visible to supervisor / UI (scale contracts ChildRow)."""

    id: str
    mode: str
    provider: str
    parent_id: str
    run_id: str
    label: str | None = None
    activity: str = "running"  # running | idle | ready | inactive | done
    depth: int | None = None


@dataclass(frozen=True)
class JobRow:
    job_id: str
    kind: str
    activity: str = "running"  # running | done
    stop_reason: str | None = None
    output: str | None = None


@dataclass
class SessionTree:
    """In-memory tree derived from a parent session's journal envelopes."""

    parent_session_id: str
    children: dict[str, ChildRow]
    jobs: dict[str, JobRow]
    unpaired_run_ids: frozenset[str]
    open_job_ids: frozenset[str]

    def list_children(self, *, scope: str = "direct") -> list[ChildRow]:
        """``direct`` = children of this parent; ``descendants`` reserved (same for MVP)."""
        if scope not in ("direct", "descendants"):
            raise ValueError(f"unknown scope: {scope!r}")
        return [
            c
            for c in self.children.values()
            if c.parent_id == self.parent_session_id
        ]



def build_session_tree(parent_session_id: str, envelopes: list[Envelope]) -> SessionTree:
    """Fold SubagentStart/End and JobStart/End into a SessionTree."""
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
                activity="running",
            )
            open_runs[p.run_id] = p.child_session_id
        elif isinstance(p, SubagentEnd):
            cid = open_runs.pop(p.run_id, None)
            if cid is None:
                # end without start — still record inactive stub
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
                    activity="done",
                    depth=prev.depth,
                )
        elif isinstance(p, JobStart):
            jobs[p.job_id] = JobRow(job_id=p.job_id, kind=p.kind, activity="running")
            open_jobs.add(p.job_id)
        elif isinstance(p, JobEnd):
            open_jobs.discard(p.job_id)
            prev_j = jobs.get(p.job_id)
            jobs[p.job_id] = JobRow(
                job_id=p.job_id,
                kind=prev_j.kind if prev_j else "unknown",
                activity="done",
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
    """SubagentStart payloads whose run_id has no matching SubagentEnd."""
    ended: set[str] = set()
    starts: dict[str, SubagentStart] = {}
    for env in envelopes:
        if isinstance(env.payload, SubagentStart):
            starts[env.payload.run_id] = env.payload
        elif isinstance(env.payload, SubagentEnd):
            ended.add(env.payload.run_id)
    return [starts[rid] for rid in starts if rid not in ended]


def unpaired_compaction_starts(envelopes: list[Envelope]) -> list[CompactionStart]:
    """CompactionStart payloads whose compaction_id has no matching CompactionEnd."""
    ended: set[str] = set()
    starts: dict[str, CompactionStart] = {}
    for env in envelopes:
        if isinstance(env.payload, CompactionStart):
            starts[env.payload.compaction_id] = env.payload
        elif isinstance(env.payload, CompactionEnd):
            ended.add(env.payload.compaction_id)
    return [starts[cid] for cid in starts if cid not in ended]
