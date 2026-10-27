"""Self-improvement, gated: learn a change from the traces, prove it on saved runs, ask a person.

1. **Reflect.** The supervisor reads the agent's issues (failing checks, judges' fails and
   their reasons, runs people marked wrong) and proposes edits to the live version's prompt.
2. **Candidate.** The edited manifest is published as a candidate: its artifacts exist and a
   session can be pinned to them, but it is not a release and nothing runs it unasked.
3. **Measure.** The chosen dataset is replayed on the live version (or a finished replay of
   it is reused) and on the candidate, item by item, as any experiment.
4. **Gate.** The candidate is refused if it does worse on any saved run, answers fewer, or
   fails more checks or judges. Refused candidates never reach anyone's inbox.
5. **Approve.** One that passes waits in Approvals with its diff, its evidence and its
   results. Approving makes that exact candidate the next version; nothing is rebuilt.

The optimizer can only change the prompt. It never sees the datasets it is measured on (the
replays' sessions are excluded from its evidence), and cannot change a judge, a check or a
dataset, so it cannot grade its own work.
"""

from __future__ import annotations

import logging
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
)
from prime_agent.schemas.harness_manifest import HarnessManifest, PromptSpec, manifest_body
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.evaluation_service import ITEM_TIMEOUT_S, EvaluationService, Pin, SessionFactory
from prime_agent.services.harness_publish_service import HarnessPublisher, PublishError

__all__ = ["ACTIVE", "ImprovementNotFoundError", "ImprovementService", "decide_gate"]

logger = logging.getLogger(__name__)

ACTIVE = ("drafting", "evaluating")
"""A proposal still being worked on; an agent has at most one."""
EVIDENCE_DAYS = 30


class ImprovementNotFoundError(Exception):
    """No such proposal, agent or dataset for this user."""


def _now() -> datetime:
    return datetime.now(UTC)


def _judges(result: ExperimentItemResult | None) -> set[str]:
    return {v.name for v in result.verdicts if v.source == "judge"} if result else set()


def decide_gate(cmp: ExperimentComparison) -> Gate:
    """Whether the candidate (``cmp.b``) may replace the live version (``cmp.a``).

    It passes only when every saved run finished and was judged on both, it did better on at least one and
    worse on none, answered at least as many, and failed no more checks or judges than the
    live version. A change that improves nothing the runs show is not worth a person's time.
    """
    a, b = cmp.a.stats, cmp.b.stats
    items = [GateItem(item_id=r.item_id, input=r.input, change=r.change) for r in cmp.rows]
    reasons: list[str] = []
    unknown = sum(1 for r in cmp.rows if r.change == "unknown")
    if not cmp.rows or a is None or b is None:
        return Gate(passed=False, reasons=["nothing was replayed"], better=0, worse=0, items=items)
    if unknown:
        reasons.append(f"{unknown} saved run{'s' if unknown != 1 else ''} did not finish on both versions")
    unjudged = sum(1 for r in cmp.rows if r.change != "unknown" and _judges(r.a) != _judges(r.b))
    if unjudged:
        reasons.append(f"{unjudged} saved run{' was' if unjudged == 1 else 's were'} not judged on both versions")
    if cmp.worse:
        reasons.append(f"it did worse on {cmp.worse} of {len(cmp.rows)} saved runs")
    elif not cmp.better and not unknown:
        reasons.append(f"it did no better on any of {len(cmp.rows)} saved runs")
    if b.answered < a.answered:
        reasons.append(f"it answered {b.answered} where the live version answered {a.answered}")
    if b.checks_failed > a.checks_failed:
        reasons.append(f"{b.checks_failed} runs failed checks, against {a.checks_failed}")
    if b.judge_fail > a.judge_fail:
        reasons.append(f"judges failed it {b.judge_fail} times, against {a.judge_fail}")
    passed = not reasons
    if passed:
        reasons.append(f"better on {cmp.better} and worse on none of {len(cmp.rows)} saved runs")
    return Gate(passed=passed, reasons=reasons, better=cmp.better, worse=cmp.worse, items=items)


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
        self, *, user_id: str, agent_id: str, dataset_id: str, notes: str | None, db: AsyncSession
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
                       notes=(notes or "").strip() or None, base_version=live.version, base_manifest=live.manifest)
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

    async def _replay_sessions(self, db: AsyncSession, agent_id: str) -> list[str]:
        """Supervisor ids of every session an experiment of this agent replayed in."""
        rows = await db.execute(
            select(SessionLink.ai_session_id)
            .join(ExperimentRun, ExperimentRun.session_id == SessionLink.id)
            .join(Experiment, Experiment.id == ExperimentRun.experiment_id)
            .where(Experiment.harness_id == agent_id)
        )
        return [str(s) for s in rows.scalars().all() if s][:5000]

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
        agent = await db.get(Harness, row.harness_id)
        live = await self._live(db, row.harness_id)
        if agent is None or live is None:
            return await self._fail(db, row, "the agent or its live version is gone")
        base = HarnessManifest.model_validate(row.base_manifest)

        # 1. reflect: what in the prompt the failing runs point to
        since = int((_now().timestamp() - EVIDENCE_DAYS * 86400) * 1000)
        try:
            reflected = await self._client.reflect(identity, {
                "composition_id": live.composition_id,
                "persona": base.prompt.persona,
                "sections": [{"id": s.id, "text": s.text} for s in base.prompt.sections],
                "notes": row.notes,
                "since_ms": since,
                "exclude_sessions": await self._replay_sessions(db, row.harness_id),
            })
        except AiUpstreamError as exc:
            return await self._fail(db, row, f"could not learn from the traces: {exc}")
        proposal = reflected.get("proposal") or {}
        row.rationale = str(proposal.get("rationale") or "") or None
        row.changed = list(proposal.get("changed") or [])
        row.evidence = await self._evidence(db, row.user_id, list(reflected.get("evidence") or []))
        if not row.changed:
            row.status = "no_change"
            await db.commit()
            return None
        try:
            persona = str(proposal.get("persona") or base.prompt.persona)
            prompt = PromptSpec(
                persona=persona if len(persona) <= 64 else base.prompt.persona,
                sections=[{"id": s["id"], "text": s["text"]} for s in proposal.get("sections") or []],
            )
            candidate = base.model_copy(update={"prompt": prompt})
            HarnessManifest.model_validate(manifest_body(candidate))
        except (ValidationError, KeyError, TypeError) as exc:
            return await self._fail(db, row, f"the proposed prompt is not valid: {exc}"[:500])
        row.candidate_manifest = manifest_body(candidate)
        await db.commit()

        # 2. a candidate: runnable by its pin, not a release, not live
        try:
            artifacts = await self._publisher.publish_candidate(agent, candidate, tag=row.id.replace("-", "")[:8],
                                                                identity=identity, db=db)
        except PublishError as exc:
            return await self._fail(db, row, f"the candidate could not be published: {exc}")
        row.candidate, row.status = artifacts, "evaluating"
        await db.commit()

        # 3. measure: the live version (a finished replay of it, if one covers the dataset) and the candidate
        baseline = await self._reusable_baseline(db, row.dataset_id, live)
        if baseline is None:
            pin = await self._evals.pin_for(db, agent, live.version)
            baseline = await self._measure(db, row, pin, identity, session_factory, poll_s, timeout_s)
            if baseline is None:
                return None
        row.baseline_experiment_id = baseline.id
        await db.commit()
        pin = Pin(label=f"candidate {row.id[:6]}", harness_version=artifacts["harness_version"],
                  runtime=artifacts["runtime"], composition_id=artifacts["composition_id"],
                  composition_digest=artifacts["composition_digest"], release_version=None)
        measured = await self._measure(db, row, pin, identity, session_factory, poll_s, timeout_s)
        if measured is None:
            return None
        row.candidate_experiment_id = measured.id
        await db.commit()

        # 4. gate
        cmp = await self._evals.compare(identity=identity, user_id=row.user_id, a_id=baseline.id, b_id=measured.id, db=db)
        gate = decide_gate(cmp)
        await db.refresh(row)
        row.gate = gate.model_dump()
        row.status = "awaiting_approval" if gate.passed else "refused"
        await db.commit()
        return None

    async def _measure(
        self, db: AsyncSession, row: Proposal, pin: Pin, identity: TenantPrincipal,
        session_factory: SessionFactory, poll_s: float, timeout_s: float,
    ) -> Experiment | None:
        try:
            exp = await self._evals.create_experiment(user_id=row.user_id, dataset_id=row.dataset_id, pin=pin, db=db)
        except HTTPException as exc:
            detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
            await self._fail(db, row, f"could not replay the dataset: {detail.get('message') or exc.detail}")
            return None
        await self._evals.run_experiment(exp.id, identity=identity, session_factory=session_factory,
                                         poll_s=poll_s, timeout_s=timeout_s)
        await db.refresh(exp)
        if exp.status != "done":
            await self._fail(db, row, f"the replay on {pin.label} {exp.status}" + (f": {exp.error}" if exp.error else ""))
            return None
        return exp

    @staticmethod
    async def _reusable_baseline(db: AsyncSession, dataset_id: str, live: HarnessRelease) -> Experiment | None:
        """A finished replay of the live version that covers exactly the dataset's items now."""
        items = set((await db.execute(select(DatasetItem.id).where(DatasetItem.dataset_id == dataset_id))).scalars().all())
        candidates = (
            await db.execute(
                select(Experiment).where(Experiment.dataset_id == dataset_id, Experiment.status == "done",
                                         Experiment.composition_digest == live.composition_digest)
                .order_by(Experiment.created_at.desc()).limit(5)
            )
        ).scalars().all()
        for exp in candidates:
            runs = (await db.execute(select(ExperimentRun).where(ExperimentRun.experiment_id == exp.id))).scalars().all()
            if {r.item_id for r in runs} == items and all(r.status != "pending" for r in runs):
                return exp
        return None

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
