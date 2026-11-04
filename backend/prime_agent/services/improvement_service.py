"""Self-improvement, gated: learn a change from the traces, prove it on saved runs, ask a person.

1. **Reflect.** The supervisor reads the agent's issues (failing checks, judges' fails and
   their reasons, runs people marked wrong) and what became of the changes proposed before
   (``_history``), and proposes itemized edits to the live version's prompt.
2. **Candidate.** The edited manifest is published as a candidate: its artifacts exist and a
   session can be pinned to them, but it is not a release and nothing runs it unasked.
3. **Measure.** The chosen dataset is replayed on the live version and on the candidate, back
   to back, each saved run several times (trials), as any experiment.
4. **Gate.** The candidate is refused if it does worse on any saved run, answers fewer, fails
   more checks or judges, passes every trial on fewer runs, or costs or takes much more; and,
   when some saved runs are held out, if it is better on none of them. Refused candidates
   never reach anyone's inbox.
5. **Approve.** One that passes waits in Approvals with its diff, its evidence and its
   results. Approving makes that exact candidate the next version; nothing is rebuilt.

A proposal may try a few candidates (``rounds``), each editing the best one kept so far
(GEPA's loop, ``_run``); the one shown is the best-scoring kept candidate that passes the gate.

The optimizer can only change the prompt, and cannot change a judge, a check or a dataset, so
it cannot grade its own work. It never reads an experiment's replays, nor the conversations of
held-out saved runs: the runs it learns from are the agent's failures (a dataset saved from an
issue holds some of them), and the held-out ones show whether a change fixes more than what it
read. That is GEPA's split: reflective mutation reads minibatches of the train set, and a
candidate is kept on its scores on a separate validation set (``gepa.api.optimize``).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Dataset, DatasetItem, Experiment, ExperimentRun, Proposal
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.harness_release import HarnessDraft, HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.evaluation import (
    Evidence,
    EvidenceExample,
    ExperimentComparison,
    ExperimentItemResult,
    Gate,
    GateItem,
    PromptOut,
    PromptSectionOut,
    ProposalDetail,
    ProposalOut,
    SearchRound,
)
from prime_agent.schemas.harness_manifest import HarnessManifest, PromptSpec, manifest_body
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.evaluation_service import (
    ITEM_TIMEOUT_S,
    EvaluationService,
    Pin,
    SessionFactory,
)
from prime_agent.services.harness_publish_service import HarnessPublisher, PublishError

__all__ = ["ACTIVE", "ImprovementNotFoundError", "ImprovementService", "decide_gate", "pareto_parent"]

logger = logging.getLogger(__name__)

ACTIVE = ("drafting", "evaluating")
"""A proposal still being worked on; an agent has at most one."""
HISTORY = 20
"""Earlier proposals the reflection reads, the latest (``supervisor.traces.reflect.MAX_HISTORY``)."""
_SETTLED = ("awaiting_approval", "refused", "no_change", "approved", "rejected")
"""Outcomes a reflection learns from (a failed proposal says nothing about the prompt)."""
EVIDENCE_DAYS = 30


class ImprovementNotFoundError(Exception):
    """No such proposal, agent or dataset for this user."""


def _now() -> datetime:
    return datetime.now(UTC)


def _judges(result: ExperimentItemResult | None) -> set[str]:
    return {v.name for v in result.verdicts if v.source == "judge"} if result else set()


def decide_gate(cmp: ExperimentComparison, *, max_cost_increase: float = 0.2, max_time_increase: float = 0.5) -> Gate:
    """Whether the candidate (``cmp.b``) may replace the live version (``cmp.a``).

    It passes only when every saved run finished and was judged on both, it did better on at least one and
    worse on none (and better on at least one held-out run, when some are held out), answered at least as many, failed no more checks or judges, passed every
    trial on at least as many saved runs, and costs and takes no more per run than allowed.
    A change that improves nothing the runs show is not worth a person's time.
    """
    a, b = cmp.a.stats, cmp.b.stats
    items = [GateItem(item_id=r.item_id, input=r.input, change=r.change, held_out=r.held_out) for r in cmp.rows]
    held = [r for r in cmp.rows if r.held_out]
    held_better = sum(1 for r in held if r.change == "better")
    held_worse = sum(1 for r in held if r.change == "worse")
    reasons: list[str] = []
    unknown = sum(1 for r in cmp.rows if r.change == "unknown")
    if not cmp.rows or a is None or b is None:
        return Gate(passed=False, reasons=["nothing was replayed"], better=0, worse=0, items=items)
    gate = {"better": cmp.better, "worse": cmp.worse, "items": items, "held_out_better": held_better,
            "held_out_worse": held_worse}
    if unknown:
        reasons.append(f"{unknown} saved run{'s' if unknown != 1 else ''} did not finish on both versions")
    unjudged = sum(1 for r in cmp.rows if r.change != "unknown" and _judges(r.a) != _judges(r.b))
    if unjudged:
        reasons.append(f"{unjudged} saved run{' was' if unjudged == 1 else 's were'} not judged on both versions")
    if cmp.worse:
        reasons.append(f"it did worse on {cmp.worse} of {len(cmp.rows)} saved runs")
    elif not cmp.better and not unknown:
        reasons.append(f"it did no better on any of {len(cmp.rows)} saved runs")
    elif held and not held_better and not unknown:
        # better only where it could have fitted: what it read, not what it will meet
        reasons.append(f"it did no better on any of the {len(held)} held-out saved runs, only on runs it learnt from")
    if b.answered < a.answered:
        reasons.append(f"it answered {b.answered} where the live version answered {a.answered}")
    if b.checks_failed > a.checks_failed:
        reasons.append(f"{b.checks_failed} runs failed checks, against {a.checks_failed}")
    if b.judge_fail > a.judge_fail:
        reasons.append(f"judges failed it {b.judge_fail} times, against {a.judge_fail}")
    if b.pass_all < a.pass_all:  # pass^k: consistency, what a person relies on
        reasons.append(f"it passed every trial on {b.pass_all} saved runs, against {a.pass_all}")
    # What a run costs and takes is part of how good it is: a fix that doubles either is a trade
    # the owner decides on (``improvement_max_*_increase``), not one the gate makes for them.
    if a.avg_cost and b.avg_cost is not None and b.avg_cost > a.avg_cost * (1 + max_cost_increase):
        reasons.append(f"a run costs {b.avg_cost / a.avg_cost:.1f}× the live version's "
                       f"(at most {1 + max_cost_increase:.1f}× allowed)")
    if a.avg_duration_ms and b.avg_duration_ms is not None and b.avg_duration_ms > a.avg_duration_ms * (1 + max_time_increase):
        reasons.append(f"a run takes {b.avg_duration_ms / a.avg_duration_ms:.1f}× as long as the live version's "
                       f"(at most {1 + max_time_increase:.1f}× allowed)")
    passed = not reasons
    if passed:
        reasons.append(f"better on {cmp.better} and worse on none of {len(cmp.rows)} saved runs"
                       + (f", {held_better} of the {len(held)} held out" if held else ", none of them held out"))
    return Gate(passed=passed, reasons=reasons, **gate)


@dataclass
class _Member:
    """A version in the search's pool: the live one (round 0) or a candidate kept."""

    round: int
    manifest: HarnessManifest
    artifacts: dict[str, str] | None
    experiment_id: str | None
    scores: dict[str, float | None]


def _total(scores: dict[str, float | None], items: list[str] | None = None) -> float | None:
    """The sum of the scores on ``items`` (all of them by default); None if one is unfinished."""
    picked = [scores.get(i) for i in (items if items is not None else list(scores))]
    if any(v is None for v in picked):
        return None
    return float(sum(v for v in picked if v is not None))


def pareto_parent(scores: list[dict[str, float | None]], held_out: set[str], rng: random.Random) -> int:
    """Which version of the pool the next candidate edits: GEPA's
    ``select_program_candidate_from_pareto_front`` (``gepa/gepa_utils.py``). For each saved
    run (the held-out ones when there are some: GEPA's valset), the versions with its best
    score form a front; versions on no front only by others' grace are dropped
    (``remove_dominated_programs``: lowest total first, while every front it is on keeps
    another); the parent is drawn weighted by how many fronts each remaining one is on."""
    items = sorted({i for s in scores for i in s if not held_out or i in held_out})
    fronts: dict[str, set[int]] = {}
    for item in items:
        values = [(n, s.get(item)) for n, s in enumerate(scores)]
        best = max((v for _, v in values if v is not None), default=None)
        if best is not None:
            fronts[item] = {n for n, v in values if v == best}
    if not fronts:
        return 0
    totals = [_total(s, items) or 0.0 for s in scores]
    on: dict[int, list[str]] = {}
    for item, front in fronts.items():
        for n in front:
            on.setdefault(n, []).append(item)
    left = {item: len(front) for item, front in fronts.items()}
    dominated: set[int] = set()
    for n in sorted(on, key=lambda m: totals[m]):
        if all(left[item] > 1 for item in on[n]):
            dominated.add(n)
            for item in on[n]:
                left[item] -= 1
    draw = [n for item in sorted(fronts) for n in sorted(fronts[item]) if n not in dominated]
    return rng.choice(draw)


def _round_feedback(record: dict[str, Any]) -> dict[str, Any]:
    """An earlier round of this search as the reflection reads it (with the gate's history)."""
    return {
        "outcome": "kept in this search" if record.get("kept") else "dropped in this search",
        "rationale": record.get("rationale"),
        "edits": [{k: e.get(k) for k in ("action", "section", "text")} for e in record.get("edits") or []][:20],
        "why": [record["why"]] if record.get("why") else [],
    }


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in ("round", "parent", "rationale", "edits", "experiment_id", "kept", "why")}


def _prompt(manifest: dict[str, Any] | None) -> PromptOut | None:
    if not manifest:
        return None
    p = manifest.get("prompt") or {}
    return PromptOut(
        persona=str(p.get("persona") or ""),
        sections=[PromptSectionOut(id=str(s.get("id")), text=str(s.get("text") or "")) for s in p.get("sections") or []],
    )


class ImprovementService:
    def __init__(self, settings: Settings, *, client: AiClient | None = None) -> None:
        self._settings = settings
        self._client = client or AiClient.from_settings(settings)
        self._evals = EvaluationService(settings, client=self._client)
        self._publisher = HarnessPublisher(self._client)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ImprovementService:
        return cls(settings or get_settings())

    # --- reading --------------------------------------------------------------------

    @staticmethod
    async def _proposal(db: AsyncSession, user_id: str, proposal_id: str) -> Proposal:
        row = (
            await db.execute(select(Proposal).where(Proposal.id == proposal_id, Proposal.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            raise ImprovementNotFoundError()
        return row

    async def _out(self, row: Proposal, db: AsyncSession) -> ProposalOut:
        agent = await db.get(Harness, row.harness_id)
        dataset = await db.get(Dataset, row.dataset_id)
        return ProposalOut(
            id=row.id, agent_id=row.harness_id, agent_name=agent.name if agent else None, dataset_id=row.dataset_id,
            dataset_name=dataset.name if dataset else None, status=row.status, error=row.error, notes=row.notes,
            base_version=row.base_version, rationale=row.rationale, changed=list(row.changed or []),
            edits=list(row.edits or []), skipped_edits=list(row.skipped_edits or []),
            expected_outcome=row.expected_outcome, max_rounds=row.max_rounds or 1,
            rounds=[SearchRound.model_validate(r) for r in row.rounds or []],
            gate=Gate.model_validate(row.gate) if row.gate else None, release_version=row.release_version,
            created_at=row.created_at, decided_at=row.decided_at,
        )

    async def list_proposals(
        self, *, user_id: str, db: AsyncSession, agent_id: str | None = None, status: str | None = None
    ) -> list[ProposalOut]:
        query = select(Proposal).where(Proposal.user_id == user_id)
        if agent_id:
            query = query.where(Proposal.harness_id == agent_id)
        if status:
            query = query.where(Proposal.status == status)
        rows = (await db.execute(query.order_by(Proposal.created_at.desc()).limit(100))).scalars().all()
        return [await self._out(r, db) for r in rows]

    async def detail(
        self, *, identity: TenantPrincipal, user_id: str, proposal_id: str, db: AsyncSession
    ) -> ProposalDetail:
        row = await self._proposal(db, user_id, proposal_id)
        out = await self._out(row, db)
        base = _prompt(row.base_manifest)
        assert base is not None
        experiments: dict[str, Any] = {}
        for key, exp_id in (("baseline", row.baseline_experiment_id), ("candidate", row.candidate_experiment_id)):
            if exp_id:
                try:
                    experiments[key] = (
                        await self._evals.experiment(identity=identity, user_id=user_id, experiment_id=exp_id, db=db)
                    ).experiment
                except Exception:  # an experiment removed with its dataset: show the rest
                    logger.debug("proposal %s: experiment %s unreadable", row.id, exp_id, exc_info=True)
        return ProposalDetail(
            **out.model_dump(), base_prompt=base, candidate_prompt=_prompt(row.candidate_manifest),
            evidence=[Evidence.model_validate(e) for e in row.evidence or []],
            baseline=experiments.get("baseline"), candidate=experiments.get("candidate"),
        )

    # --- proposing ------------------------------------------------------------------

    async def propose(
        self, *, user_id: str, agent_id: str, dataset_id: str, notes: str | None, db: AsyncSession, rounds: int = 1
    ) -> Proposal:
        """A proposal queued for the agent (``run`` drafts, measures and gates it)."""
        agent = (
            await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
        ).scalar_one_or_none()
        dataset = await db.get(Dataset, dataset_id)
        if agent is None or dataset is None or dataset.user_id != user_id or dataset.harness_id != agent_id:
            raise ImprovementNotFoundError()
        has_items = (await db.execute(select(DatasetItem.id).where(DatasetItem.dataset_id == dataset_id).limit(1))).first()
        if has_items is None:
            raise HTTPException(status_code=409, detail={"error_code": "EMPTY_DATASET",
                                                         "message": "save some runs to the dataset first: they are what a change is measured on"})
        busy = (
            await db.execute(select(Proposal.id).where(Proposal.harness_id == agent_id, Proposal.status.in_(ACTIVE)))
        ).first()
        if busy is not None:
            raise HTTPException(status_code=409, detail={"error_code": "PROPOSAL_RUNNING",
                                                         "message": "a change for this agent is still being worked on"})
        live = await self._live(db, agent_id)
        if live is None:
            raise HTTPException(status_code=409, detail={"error_code": "NO_SUCH_VERSION", "message": "publish the agent first"})
        row = Proposal(user_id=user_id, harness_id=agent_id, dataset_id=dataset_id, status="drafting",
                       notes=(notes or "").strip() or None, base_version=live.version, base_manifest=live.manifest,
                       max_rounds=rounds)
        db.add(row)
        await db.commit()
        return row

    @staticmethod
    async def _live(db: AsyncSession, agent_id: str) -> HarnessRelease | None:
        return (
            await db.execute(
                select(HarnessRelease).where(HarnessRelease.harness_id == agent_id)
                .order_by(HarnessRelease.version.desc()).limit(1)
            )
        ).scalar_one_or_none()

    async def _unseen_sessions(self, db: AsyncSession, agent_id: str) -> list[str]:
        """Supervisor ids of the sessions the optimizer must not read: each held-out saved run's
        (first, so a cap never drops them) and every one an experiment of this agent replayed in."""
        held = await db.execute(
            select(SessionLink.ai_session_id)
            .join(DatasetItem, DatasetItem.source_session_id == SessionLink.id)
            .join(Dataset, Dataset.id == DatasetItem.dataset_id)
            .where(Dataset.harness_id == agent_id, DatasetItem.held_out.is_(True))
        )
        replays = await db.execute(
            select(SessionLink.ai_session_id)
            .join(ExperimentRun, ExperimentRun.session_id == SessionLink.id)
            .join(Experiment, Experiment.id == ExperimentRun.experiment_id)
            .where(Experiment.harness_id == agent_id)
        )
        ids = [str(s) for s in [*held.scalars().all(), *replays.scalars().all()] if s]
        return list(dict.fromkeys(ids))[:5000]

    async def _evidence(self, db: AsyncSession, user_id: str, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The supervisor's evidence with product session ids (examples the user cannot open are dropped)."""
        ai_ids = {e.get("session_id") for i in raw for e in i.get("examples") or [] if e.get("session_id")}
        links = {}
        if ai_ids:
            found = await db.execute(
                select(SessionLink.ai_session_id, SessionLink.id)
                .where(SessionLink.user_id == user_id, SessionLink.ai_session_id.in_(ai_ids))
            )
            links = dict(found.all())
        out: list[dict[str, Any]] = []
        for issue in raw:
            examples = []
            for e in issue.get("examples") or []:
                sid = links.get(e.get("session_id"))
                examples.append(EvidenceExample(session_id=sid, run_id=e.get("run_id") if sid else None,
                                                input=e.get("input"), summary=e.get("summary"),
                                                reason=e.get("reason")).model_dump())
            out.append(Evidence(title=str(issue.get("title") or ""), runs=int(issue.get("runs") or 0),
                                examples=examples).model_dump())
        return out

    @staticmethod
    async def _history(db: AsyncSession, row: Proposal) -> list[dict[str, Any]]:
        """The agent's earlier proposals, oldest first, each with what became of it: so a change
        the gate refused or a person rejected is not proposed again, and one that was approved
        but did not fix its issue is revised (Prime Agent's ``/refine`` plans from its
        refinement history the same way: ``history_for_prompt``, the latest 20)."""
        rows = (
            await db.execute(
                select(Proposal)
                .where(Proposal.harness_id == row.harness_id, Proposal.id != row.id, Proposal.status.in_(_SETTLED))
                .order_by(Proposal.created_at.desc())
                .limit(HISTORY)
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for past in reversed(rows):
            reasons = list((past.gate or {}).get("reasons") or [])
            gate = reasons if (past.gate or {}).get("passed") else [f"the gate refused it: {r}" for r in reasons]
            why = {
                "approved": [f"a person approved it: it became v{past.release_version}", *gate],
                # a refused change can be dismissed too: what the gate said is what counts
                "rejected": [*(["it passed the gate, but a person rejected it"] if (past.gate or {}).get("passed") else []),
                             *gate],
                "refused": gate,
                "awaiting_approval": ["it passed the gate and waits for a person", *gate],
            }.get(past.status, [])
            out.append({
                "outcome": past.status,
                "rationale": past.rationale,
                "edits": [{k: e.get(k) for k in ("action", "section", "text")} for e in past.edits or []][:20],
                "why": why[:10],
            })
        return out

    async def _fail(self, db: AsyncSession, row: Proposal, message: str, status: str = "failed") -> None:
        row.status, row.error = status, message[:1000]
        await db.commit()

    async def run(
        self,
        proposal_id: str,
        *,
        identity: TenantPrincipal,
        session_factory: SessionFactory,
        poll_s: float = 1.0,
        timeout_s: float = ITEM_TIMEOUT_S,
    ) -> None:
        """Draft, publish, measure and gate one proposal (a background task)."""
        async with session_factory() as db:
            row = await db.get(Proposal, proposal_id)
            if row is None or row.status != "drafting":
                return
            try:
                await self._run(row, identity, db, session_factory, poll_s=poll_s, timeout_s=timeout_s)
            except Exception as exc:
                logger.exception("proposal %s failed", proposal_id)
                await db.rollback()
                row = await db.get(Proposal, proposal_id)
                if row is not None and row.status in ACTIVE:
                    await self._fail(db, row, str(exc) or type(exc).__name__)

    async def _run(
        self, row: Proposal, identity: TenantPrincipal, db: AsyncSession, session_factory: SessionFactory, *,
        poll_s: float, timeout_s: float,
    ) -> None:
        """Search for a better prompt, then gate the best candidate found.

        With one round (the default) this is: reflect, publish a candidate, replay the live
        version and the candidate, gate. With more, it is GEPA's loop (``gepa.core.engine``):
        each round picks a parent from the candidates kept so far by Pareto front over the
        held-out runs (``pareto_parent``), reflects on it with what earlier rounds did, and
        keeps the new candidate only if it beats its parent on the runs it may learn from
        (``StrictImprovementAcceptance``). The kept candidate with the best score that passes
        the gate is the one a person sees.
        """
        agent = await db.get(Harness, row.harness_id)
        live = await self._live(db, row.harness_id)
        if agent is None or live is None:
            return await self._fail(db, row, "the agent or its live version is gone")
        base = HarnessManifest.model_validate(row.base_manifest)
        since = int((_now().timestamp() - EVIDENCE_DAYS * 86400) * 1000)
        held = await self._evals.held_out_items(db, row.dataset_id)
        history = await self._history(db, row)
        rng = random.Random(row.id)  # a search replays the same way: reproducible parents
        pool: list[_Member] = [_Member(round=0, manifest=base, artifacts=None, experiment_id=None, scores={})]
        rounds: list[dict[str, Any]] = []
        baseline: Experiment | None = None
        for n in range(1, max(1, row.max_rounds) + 1):
            parent = pool[0] if len(pool) == 1 else pool[pareto_parent([m.scores for m in pool], held, rng)]
            # 1. reflect: what in the parent's prompt the failing runs point to
            try:
                reflected = await self._client.reflect(identity, {
                    "composition_id": live.composition_id,
                    "persona": parent.manifest.prompt.persona,
                    "sections": [{"id": s.id, "text": s.text} for s in parent.manifest.prompt.sections],
                    "notes": row.notes,
                    "since_ms": since,
                    "exclude_sessions": await self._unseen_sessions(db, row.harness_id),
                    "history": [*history, *(_round_feedback(r) for r in rounds)][-HISTORY:],
                })
            except AiUpstreamError as exc:
                if rounds:  # what earlier rounds found still stands
                    break
                return await self._fail(db, row, f"could not learn from the traces: {exc}")
            proposal = reflected.get("proposal") or {}
            if n == 1:
                row.evidence = await self._evidence(db, row.user_id, list(reflected.get("evidence") or []))
            if not list(proposal.get("changed") or []):
                if rounds:
                    break  # nothing more to try: decide on what was found
                row.rationale = str(proposal.get("rationale") or "") or None
                row.status, row.changed = "no_change", []
                await db.commit()
                return None
            try:
                persona = str(proposal.get("persona") or parent.manifest.prompt.persona)
                prompt = PromptSpec(
                    persona=persona if len(persona) <= 64 else parent.manifest.prompt.persona,
                    sections=[{"id": s["id"], "text": s["text"]} for s in proposal.get("sections") or []],
                )
                candidate = parent.manifest.model_copy(update={"prompt": prompt})
                HarnessManifest.model_validate(manifest_body(candidate))
            except (ValidationError, KeyError, TypeError) as exc:
                if rounds:
                    break
                return await self._fail(db, row, f"the proposed prompt is not valid: {exc}"[:500])
            record: dict[str, Any] = {
                "round": n, "parent": parent.round, "rationale": str(proposal.get("rationale") or "") or None,
                "edits": [e for e in proposal.get("edits") or [] if isinstance(e, dict)],
                "skipped": [str(x) for x in proposal.get("skipped") or []],
                "expected_outcome": str(proposal.get("expected_outcome") or "") or None,
                "changed": list(proposal.get("changed") or []),
                "manifest": manifest_body(candidate), "experiment_id": None, "kept": False, "why": "",
            }
            rounds.append(record)
            row.rounds = [_public(r) for r in rounds]
            if n == 1:
                self._show(row, record)
            await db.commit()

            # 2. a candidate: runnable by its pin, not a release, not live
            tag = row.id.replace("-", "")[:8] + ("" if n == 1 else f"r{n}")
            try:
                artifacts = await self._publisher.publish_candidate(agent, candidate, tag=tag, identity=identity, db=db)
            except PublishError as exc:
                if n > 1:
                    record["why"] = f"could not be published: {exc}"[:300]
                    break
                return await self._fail(db, row, f"the candidate could not be published: {exc}")
            if n == 1:
                row.candidate = artifacts
            row.status = "evaluating"
            await db.commit()

            # 3. measure. The live version is replayed once per search, here, not reused from an
            # older replay: the model behind it may answer differently now, and both sides of a
            # comparison should meet the same provider (and the same judges).
            if baseline is None:
                pin = await self._evals.pin_for(db, agent, live.version)
                baseline = await self._measure(db, row, pin, identity, session_factory, poll_s, timeout_s)
                if baseline is None:
                    return None
                row.baseline_experiment_id = baseline.id
                pool[0].experiment_id = baseline.id
                pool[0].scores = await self._evals.item_scores(identity=identity, user_id=row.user_id,
                                                               experiment_id=baseline.id, db=db)
                await db.commit()
            label = f"candidate {row.id[:6]}" + ("" if n == 1 else f" round {n}")
            pin = Pin(label=label, harness_version=artifacts["harness_version"], runtime=artifacts["runtime"],
                      composition_id=artifacts["composition_id"], composition_digest=artifacts["composition_digest"],
                      release_version=None)
            measured = await self._measure(db, row, pin, identity, session_factory, poll_s, timeout_s,
                                           required=n == 1)
            if measured is None:
                if n == 1:
                    return None
                record["why"] = "its replay did not finish"  # ends the search; earlier rounds stand
                break
            record["experiment_id"] = measured.id
            if n == 1:
                row.candidate_experiment_id = measured.id
            scores = await self._evals.item_scores(identity=identity, user_id=row.user_id, experiment_id=measured.id,
                                                   db=db)
            learnt = [i for i in parent.scores if i not in held]
            before, after = _total(parent.scores, learnt), _total(scores, learnt)
            record["kept"] = after is not None and before is not None and after > before
            record["why"] = (
                "its replay did not finish on every saved run" if after is None or before is None
                else f"scored {after:g} against its parent's {before:g} on the {len(learnt)} saved runs it may learn from"
            )
            if record["kept"]:
                pool.append(_Member(round=n, manifest=candidate, artifacts=artifacts, experiment_id=measured.id,
                                    scores=scores))
            row.rounds = [_public(r) for r in rounds]
            await db.commit()

        # 4. gate the best candidate kept (or, when none was, the first one tried)
        if baseline is None:
            return await self._fail(db, row, "no candidate was measured")
        chosen, gate = await self._choose(row, identity, db, baseline, pool, rounds)
        await db.refresh(row)
        if chosen is not None:
            self._show(row, chosen)
            row.candidate_experiment_id = chosen["experiment_id"]
            row.candidate = chosen["artifacts"]
        row.rounds = [_public(r) for r in rounds]
        row.gate = gate.model_dump()
        row.status = "awaiting_approval" if gate.passed else "refused"
        await db.commit()
        return None

    async def _choose(
        self, row: Proposal, identity: TenantPrincipal, db: AsyncSession, baseline: Experiment,
        pool: list[_Member], rounds: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, Gate]:
        """The candidate to show: the best-scoring kept one that passes the gate; else the first
        tried, with its gate (a refusal says why the plain change was not enough)."""
        by_round = {r["round"]: r for r in rounds}
        kept = sorted((m for m in pool[1:] if m.experiment_id), key=lambda m: (-(_total(m.scores) or 0), m.round))
        first = rounds[0]
        tried = [*kept] + ([] if any(m.round == 1 for m in kept) else [None])
        verdict: Gate | None = None
        for member in tried:
            record = by_round[member.round] if member else first
            if not record.get("experiment_id"):
                continue
            cmp = await self._evals.compare(identity=identity, user_id=row.user_id, a_id=baseline.id,
                                            b_id=record["experiment_id"], db=db)
            gate = decide_gate(cmp, max_cost_increase=self._settings.improvement_max_cost_increase,
                               max_time_increase=self._settings.improvement_max_time_increase)
            if gate.passed:
                return {**record, "artifacts": member.artifacts if member else row.candidate}, gate
            if record is first:
                verdict = gate
        if verdict is None:
            return None, Gate(passed=False, reasons=["no candidate finished its replay"], better=0, worse=0)
        return {**first, "artifacts": row.candidate}, verdict

    @staticmethod
    def _show(row: Proposal, record: dict[str, Any]) -> None:
        """Put a round's candidate on the proposal (what its page shows and approval publishes)."""
        row.rationale, row.changed = record["rationale"], record["changed"]
        row.edits, row.skipped_edits = record["edits"], record["skipped"]
        row.expected_outcome, row.candidate_manifest = record["expected_outcome"], record["manifest"]

    async def _measure(
        self, db: AsyncSession, row: Proposal, pin: Pin, identity: TenantPrincipal,
        session_factory: SessionFactory, poll_s: float, timeout_s: float, *, required: bool = True,
    ) -> Experiment | None:
        """The dataset replayed on ``pin``; None if it did not finish (and, when ``required``,
        the proposal failed with why)."""
        try:
            exp = await self._evals.create_experiment(user_id=row.user_id, dataset_id=row.dataset_id, pin=pin, db=db)
        except HTTPException as exc:
            detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
            if required:
                await self._fail(db, row, f"could not replay the dataset: {detail.get('message') or exc.detail}")
            return None
        await self._evals.run_experiment(exp.id, identity=identity, session_factory=session_factory,
                                         poll_s=poll_s, timeout_s=timeout_s)
        await db.refresh(exp)
        if exp.status != "done":
            if required:
                await self._fail(db, row, f"the replay on {pin.label} {exp.status}" + (f": {exp.error}" if exp.error else ""))
            return None
        return exp

    # --- deciding -------------------------------------------------------------------

    async def approve(self, *, user_id: str, proposal_id: str, db: AsyncSession) -> ProposalOut:
        """Make the candidate, exactly as measured, the agent's next version."""
        row = await self._proposal(db, user_id, proposal_id)
        if row.status != "awaiting_approval" or not row.candidate or not row.candidate_manifest:
            raise HTTPException(status_code=409, detail={"error_code": "NOT_AWAITING_APPROVAL",
                                                         "message": "only a change that passed its gate can be approved"})
        agent = await db.get(Harness, row.harness_id)
        live = await self._live(db, row.harness_id)
        if agent is None or live is None:
            raise ImprovementNotFoundError()
        if live.version != row.base_version:
            raise HTTPException(status_code=409, detail={"error_code": "STALE_PROPOSAL",
                                                         "message": f"the agent is at v{live.version} now; this change was measured against v{row.base_version}"})
        candidate = HarnessManifest.model_validate(row.candidate_manifest)
        draft = await db.get(HarnessDraft, row.harness_id)
        untouched = draft is None or draft.manifest == row.base_manifest
        published = await self._publisher.promote(agent, candidate, dict(row.candidate), user_id=user_id, db=db)
        if untouched:  # an unpublished draft of the person's own is never overwritten
            if draft is None:
                db.add(HarnessDraft(harness_id=row.harness_id, manifest=row.candidate_manifest))
            else:
                draft.manifest = row.candidate_manifest
        row.status, row.release_version, row.decided_at = "approved", published.release.version, _now()
        await db.commit()
        return await self._out(row, db)

    async def reject(self, *, user_id: str, proposal_id: str, db: AsyncSession) -> ProposalOut:
        row = await self._proposal(db, user_id, proposal_id)
        if row.status not in ("awaiting_approval", "refused", "no_change", "failed"):
            raise HTTPException(status_code=409, detail={"error_code": "NOT_DECIDABLE",
                                                         "message": "this change is still being worked on or was decided"})
        row.status, row.decided_at = "rejected", _now()
        await db.commit()
        return await self._out(row, db)
