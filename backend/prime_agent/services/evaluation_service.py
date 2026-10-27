"""Datasets of saved runs, and experiments that replay them on a chosen version.

Saving a run keeps what was asked (the run's full first message) and, if the person says,
what a good answer does. An experiment replays each item of a dataset on one exact version
of the agent, each in a fresh session of the user's (so its trace, checks and judges are the
same as any run's), one item after another, and ends each session when its run is done.

An experiment never approves anything: a run that stops for an approval is recorded as
``needs_approval`` and cancelled, so replaying a dataset cannot run a tool nobody allowed.
Results are read back from the runs' traces, so they are exactly what the run pages show.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Dataset, DatasetItem, Experiment, ExperimentRun
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.harness_release import HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.evaluation import (
    ComparisonRow,
    DatasetDetail,
    DatasetItemOut,
    DatasetOut,
    ExperimentComparison,
    ExperimentDetail,
    ExperimentItemResult,
    ExperimentOut,
    ExperimentStats,
    Verdict,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.trace_service import TraceNotFoundError, TraceService

__all__ = ["MAX_ITEMS", "EvaluationNotFoundError", "EvaluationService", "Pin"]

logger = logging.getLogger(__name__)

MAX_ITEMS = 50
"""Items a dataset holds: an experiment replays them one by one."""
ITEM_TIMEOUT_S = 300.0
"""How long one replayed run may take before it is cancelled and recorded as timed out."""
STARTUP_TIMEOUT_S = 120.0
JUDGE_WAIT_S = 120.0
"""How long a finished replay waits for its agent's judges, which give verdicts after a run ends."""
STALE_AFTER = timedelta(minutes=15)
"""An experiment ``running`` with no progress for this long was cut off (the server restarted)."""

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class EvaluationNotFoundError(Exception):
    """No such dataset, item, experiment or agent for this user (or not theirs)."""


class Pin:
    """The exact version an experiment runs: what a session is created with."""

    def __init__(
        self,
        *,
        label: str,
        harness_version: str,
        runtime: str,
        composition_id: str | None,
        composition_digest: str | None,
        release_version: int | None,
    ) -> None:
        self.label = label
        self.harness_version = harness_version
        self.runtime = runtime
        self.composition_id = composition_id
        self.composition_digest = composition_digest
        self.release_version = release_version


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=UTC) if at is not None and at.tzinfo is None else at


class EvaluationService:
    def __init__(self, settings: Settings, *, client: AiClient | None = None) -> None:
        self._client = client or AiClient.from_settings(settings)
        self._traces = TraceService(settings, client=self._client)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> EvaluationService:
        return cls(settings or get_settings())

    # --- ownership ------------------------------------------------------------

    @staticmethod
    async def _agent(db: AsyncSession, user_id: str, agent_id: str) -> Harness:
        harness = (
            await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
        ).scalar_one_or_none()
        if harness is None:
            raise EvaluationNotFoundError()
        return harness

    @staticmethod
    async def _dataset(db: AsyncSession, user_id: str, dataset_id: str) -> Dataset:
        dataset = (
            await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id))
        ).scalar_one_or_none()
        if dataset is None:
            raise EvaluationNotFoundError()
        return dataset

    @staticmethod
    async def _experiment(db: AsyncSession, user_id: str, experiment_id: str) -> Experiment:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == experiment_id, Experiment.user_id == user_id))
        ).scalar_one_or_none()
        if exp is None:
            raise EvaluationNotFoundError()
        return exp

    # --- datasets ----------------------------------------------------------------

    async def list_datasets(self, *, user_id: str, agent_id: str, db: AsyncSession) -> list[DatasetOut]:
        await self._agent(db, user_id, agent_id)
        datasets = (
            await db.execute(
                select(Dataset).where(Dataset.harness_id == agent_id, Dataset.user_id == user_id).order_by(Dataset.created_at.desc())
            )
        ).scalars().all()
        ids = [d.id for d in datasets]
        items = dict(
            (await db.execute(select(DatasetItem.dataset_id, func.count()).where(DatasetItem.dataset_id.in_(ids)).group_by(DatasetItem.dataset_id))).all()
        ) if ids else {}
        runs = dict(
            (await db.execute(select(Experiment.dataset_id, func.count()).where(Experiment.dataset_id.in_(ids)).group_by(Experiment.dataset_id))).all()
        ) if ids else {}
        return [
            DatasetOut(id=d.id, agent_id=d.harness_id, name=d.name, description=d.description, items=int(items.get(d.id, 0)),
                       experiments=int(runs.get(d.id, 0)), created_at=d.created_at)
            for d in datasets
        ]

    async def create_dataset(
        self, *, user_id: str, agent_id: str, name: str, description: str | None, db: AsyncSession
    ) -> DatasetOut:
        await self._agent(db, user_id, agent_id)
        dataset = Dataset(user_id=user_id, harness_id=agent_id, name=name.strip(), description=(description or "").strip() or None)
        try:
            async with db.begin_nested():  # a clash undoes this insert only
                db.add(dataset)
            await db.commit()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail={"error_code": "DATASET_EXISTS",
                                                         "message": f"this agent already has a dataset named {name!r}"}) from exc
        return DatasetOut(id=dataset.id, agent_id=agent_id, name=dataset.name, description=dataset.description,
                          items=0, experiments=0, created_at=dataset.created_at)

    async def delete_dataset(self, *, user_id: str, dataset_id: str, db: AsyncSession) -> None:
        dataset = await self._dataset(db, user_id, dataset_id)
        await db.delete(dataset)
        await db.commit()

    async def add_run(
        self,
        *,
        identity: TenantPrincipal,
        user_id: str,
        dataset_id: str,
        session_id: str,
        run_id: str,
        expected: str | None,
        db: AsyncSession,
    ) -> DatasetItemOut:
        """Save one of the user's runs: its first message in full, how it ended, its version."""
        dataset = await self._dataset(db, user_id, dataset_id)
        count = int((await db.execute(select(func.count()).where(DatasetItem.dataset_id == dataset_id))).scalar_one())
        if count >= MAX_ITEMS:
            raise HTTPException(status_code=409, detail={"error_code": "DATASET_FULL",
                                                         "message": f"a dataset holds at most {MAX_ITEMS} runs"})
        try:
            trace = await self._traces.run(identity=identity, user_id=user_id, session_id=session_id, run_id=run_id, db=db)
        except TraceNotFoundError as exc:
            raise EvaluationNotFoundError() from exc
        if trace.agent_id != dataset.harness_id:
            raise HTTPException(status_code=422, detail={"error_code": "OTHER_AGENT",
                                                         "message": "that run is of another agent than the dataset's"})
        root = next((s for s in trace.spans if s.get("parent_id") is None), None)
        ask: str | None = None
        if root is not None:
            span = await self._traces.span(identity=identity, user_id=user_id, session_id=session_id, run_id=run_id,
                                           span_id=str(root["id"]), db=db)
            ask = (span.get("detail") or {}).get("input")
        ask = ask or trace.rollup.get("input")
        if not ask:
            raise HTTPException(status_code=422, detail={"error_code": "NO_INPUT", "message": "that run has no message to replay"})
        item = DatasetItem(dataset_id=dataset_id, input=str(ask), expected=(expected or "").strip() or None,
                           source_session_id=session_id, source_run_id=run_id, source_version=trace.version,
                           source_status=str(trace.rollup.get("status") or "") or None)
        try:
            async with db.begin_nested():
                db.add(item)
            await db.commit()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail={"error_code": "ALREADY_SAVED",
                                                         "message": "that run is already in this dataset"}) from exc
        return DatasetItemOut.model_validate(item, from_attributes=True)

    async def delete_item(self, *, user_id: str, dataset_id: str, item_id: str, db: AsyncSession) -> None:
        await self._dataset(db, user_id, dataset_id)
        item = (
            await db.execute(select(DatasetItem).where(DatasetItem.id == item_id, DatasetItem.dataset_id == dataset_id))
        ).scalar_one_or_none()
        if item is None:
            raise EvaluationNotFoundError()
        await db.delete(item)
        await db.commit()

    async def detail(self, *, identity: TenantPrincipal, user_id: str, dataset_id: str, db: AsyncSession) -> DatasetDetail:
        dataset = await self._dataset(db, user_id, dataset_id)
        agent = await self._agent(db, user_id, dataset.harness_id)
        items = (
            await db.execute(select(DatasetItem).where(DatasetItem.dataset_id == dataset_id).order_by(DatasetItem.created_at.asc()))
        ).scalars().all()
        exps = (
            await db.execute(select(Experiment).where(Experiment.dataset_id == dataset_id).order_by(Experiment.created_at.desc()))
        ).scalars().all()
        outs = []
        for exp in exps:
            await self._settle_stale(exp, db)
            results = await self._results(identity, user_id, exp, db) if exp.status != "queued" else []
            outs.append(self._exp_out(exp, results))
        return DatasetDetail(
            id=dataset.id, agent_id=dataset.harness_id, agent_name=agent.name, name=dataset.name,
            description=dataset.description,
            items=[DatasetItemOut.model_validate(i, from_attributes=True) for i in items],
            experiments=outs,
        )

    # --- experiments -------------------------------------------------------------------

    async def pin_for(self, db: AsyncSession, agent: Harness, version: int | None) -> Pin:
        """The pin of release ``version`` (none: the live one)."""
        query = select(HarnessRelease).where(HarnessRelease.harness_id == agent.id)
        query = query.where(HarnessRelease.version == version) if version else query.order_by(HarnessRelease.version.desc())
        release = (await db.execute(query.limit(1))).scalar_one_or_none()
        if release is None:
            raise HTTPException(status_code=409, detail={"error_code": "NO_SUCH_VERSION",
                                                         "message": "publish the agent first" if not version else f"the agent has no version {version}"})
        return Pin(label=f"v{release.version}", harness_version=release.harness_version, runtime=release.runtime,
                   composition_id=release.composition_id, composition_digest=release.composition_digest,
                   release_version=release.version)

    async def create_experiment(self, *, user_id: str, dataset_id: str, pin: Pin, db: AsyncSession) -> Experiment:
        """An experiment queued for every item of the dataset (``run_experiment`` replays it)."""
        dataset = await self._dataset(db, user_id, dataset_id)
        items = (
            await db.execute(select(DatasetItem).where(DatasetItem.dataset_id == dataset_id).order_by(DatasetItem.created_at.asc()))
        ).scalars().all()
        if not items:
            raise HTTPException(status_code=409, detail={"error_code": "EMPTY_DATASET", "message": "save some runs to the dataset first"})
        busy = (
            await db.execute(select(Experiment.id).where(Experiment.dataset_id == dataset_id, Experiment.status.in_(("queued", "running"))))
        ).first()
        if busy is not None:
            raise HTTPException(status_code=409, detail={"error_code": "EXPERIMENT_RUNNING",
                                                         "message": "an experiment on this dataset is still running"})
        exp = Experiment(dataset_id=dataset_id, user_id=user_id, harness_id=dataset.harness_id, label=pin.label,
                         release_version=pin.release_version, harness_version=pin.harness_version, runtime=pin.runtime,
                         composition_id=pin.composition_id, composition_digest=pin.composition_digest, status="queued")
        db.add(exp)
        await db.flush()
        for n, item in enumerate(items):
            db.add(ExperimentRun(experiment_id=exp.id, item_id=item.id, position=n, status="pending"))
        await db.commit()
        return exp

    async def cancel(self, *, user_id: str, experiment_id: str, db: AsyncSession) -> None:
        exp = await self._experiment(db, user_id, experiment_id)
        if exp.status in ("queued", "running"):
            exp.status, exp.finished_at = "cancelled", _now()
            await db.commit()

    async def _settle_stale(self, exp: Experiment, db: AsyncSession) -> None:
        beat = _aware(exp.heartbeat_at or exp.started_at or exp.created_at)
        if exp.status in ("queued", "running") and beat is not None and _now() - beat > STALE_AFTER:
            exp.status, exp.error, exp.finished_at = "failed", "cut off: the server restarted while it ran", _now()
            await db.commit()

    async def _wait_ready(self, identity: TenantPrincipal, ai_session_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_S
        while True:
            startup = await self._client.session_startup(identity, ai_session_id)
            status = startup.get("status")
            if status == "ready":
                return
            if status in ("failed", "closed"):
                raise RuntimeError(str(startup.get("reason") or f"the session {status}"))
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("the session did not start in time")
            await asyncio.sleep(1.0)

    async def _wait_judged(
        self, identity: TenantPrincipal, exp: Experiment, link: SessionLink, run: ExperimentRun, db: AsyncSession,
        *, poll_s: float,
    ) -> None:
        """Wait until every judge that judges all of the agent's runs has given this run its
        verdict, so results compare like with like. A judge that never answers is noted."""
        if not exp.composition_id or not run.run_id:
            return
        try:
            listed = await self._client.list_judges(identity, exp.composition_id)
        except AiUpstreamError:
            return
        expected = {str(j["name"]) for j in listed.get("judges") or [] if j.get("enabled") and float(j.get("sample_rate") or 0) >= 1}
        if not expected:
            return
        deadline = asyncio.get_running_loop().time() + JUDGE_WAIT_S
        while True:
            try:
                trace = await self._traces.run(identity=identity, user_id=exp.user_id, session_id=link.id,
                                               run_id=run.run_id, db=db)
                judged = {s.name for s in trace.scores if s.source == "judge"}
            except (TraceNotFoundError, HTTPException):
                judged = set()
            if expected <= judged:
                return
            if asyncio.get_running_loop().time() > deadline:
                run.error = f"not judged by {', '.join(sorted(expected - judged))}"
                return
            await asyncio.sleep(max(poll_s, 0.05) * 2)

    async def _replay(
        self, identity: TenantPrincipal, exp: Experiment, item: DatasetItem, dataset: Dataset, run: ExperimentRun,
        db: AsyncSession, *, poll_s: float, timeout_s: float,
    ) -> None:
        upstream = await self._client.create_session(
            identity, harness_version=exp.harness_version, runtime=exp.runtime,
            composition_id=exp.composition_id, composition_digest=exp.composition_digest,
        )
        ai_session_id = str(upstream.get("session_id") or "")
        now = _now()
        link = SessionLink(
            id=str(uuid4()), user_id=exp.user_id, harness_id=exp.harness_id, ai_session_id=ai_session_id,
            tenant_id=identity.tenant_id, harness_version=exp.harness_version, runtime=exp.runtime,
            composition_id=exp.composition_id, composition_digest=exp.composition_digest, status="active",
            title=f"Experiment {exp.label} · {dataset.name} · {run.position + 1}", created_at=now, updated_at=now,
        )
        db.add(link)
        run.session_id, run.status, run.started_at = link.id, "running", now
        await db.commit()
        try:
            await self._wait_ready(identity, ai_session_id)
            posted = await self._client.post_message(identity, ai_session_id, text=item.input, command_id=str(uuid4()))
            run.run_id = str(posted.get("turn_id") or "")
            await db.commit()
            deadline = asyncio.get_running_loop().time() + timeout_s
            while True:
                state = await self._client.get_run(identity, ai_session_id, run.run_id)
                status = str(state.get("status") or "")
                if status in ("completed", "failed", "cancelled", "incomplete"):
                    run.status = "done"
                    await self._wait_judged(identity, exp, link, run, db, poll_s=poll_s)
                    break
                if status == "requires_action" or state.get("pending_approval_id"):
                    await self._client.cancel_run(identity, ai_session_id, run.run_id)
                    run.status, run.error = "needs_approval", "stopped for an approval: experiments never approve"
                    break
                if asyncio.get_running_loop().time() > deadline:
                    await self._client.cancel_run(identity, ai_session_id, run.run_id)
                    run.status, run.error = "timed_out", f"no answer in {int(timeout_s)} s"
                    break
                await asyncio.sleep(poll_s)
        finally:
            try:
                await self._client.terminate_session(identity, ai_session_id)
                link.status = "terminated"
            except AiUpstreamError:
                logger.warning("could not end experiment session %s", ai_session_id)
            run.finished_at = _now()
            await db.commit()

    async def run_experiment(
        self,
        experiment_id: str,
        *,
        identity: TenantPrincipal,
        session_factory: SessionFactory,
        poll_s: float = 1.0,
        timeout_s: float = ITEM_TIMEOUT_S,
    ) -> None:
        """Replay every pending item; safe to call once per experiment (a background task)."""
        async with session_factory() as db:
            exp = await db.get(Experiment, experiment_id)
            if exp is None or exp.status not in ("queued", "running"):
                return
            dataset = await db.get(Dataset, exp.dataset_id)
            assert dataset is not None
            exp.status, exp.started_at, exp.heartbeat_at = "running", exp.started_at or _now(), _now()
            await db.commit()
            runs = (
                await db.execute(select(ExperimentRun).where(ExperimentRun.experiment_id == experiment_id).order_by(ExperimentRun.position.asc()))
            ).scalars().all()
            try:
                for run in runs:
                    await db.refresh(exp)
                    if exp.status == "cancelled":
                        return
                    if run.status != "pending":
                        continue
                    item = await db.get(DatasetItem, run.item_id)
                    if item is None:
                        run.status, run.error = "failed", "the item was removed from the dataset"
                        continue
                    try:
                        await self._replay(identity, exp, item, dataset, run, db, poll_s=poll_s, timeout_s=timeout_s)
                    except Exception as exc:  # one item failing never stops the rest
                        logger.warning("experiment %s item %s failed", experiment_id, item.id, exc_info=True)
                        run.status, run.error, run.finished_at = "failed", str(exc)[:500] or type(exc).__name__, _now()
                    exp.heartbeat_at = _now()
                    await db.commit()
                await db.refresh(exp)
                if exp.status == "running":
                    exp.status, exp.finished_at = "done", _now()
            except Exception as exc:
                logger.exception("experiment %s failed", experiment_id)
                exp.status, exp.error, exp.finished_at = "failed", str(exc)[:500], _now()
            await db.commit()

    # --- results -----------------------------------------------------------------------

    async def _results(
        self, identity: TenantPrincipal, user_id: str, exp: Experiment, db: AsyncSession
    ) -> list[ExperimentItemResult]:
        rows = (
            await db.execute(
                select(ExperimentRun, DatasetItem)
                .join(DatasetItem, DatasetItem.id == ExperimentRun.item_id)
                .where(ExperimentRun.experiment_id == exp.id)
                .order_by(ExperimentRun.position.asc())
            )
        ).all()
        out: list[ExperimentItemResult] = []
        for run, item in rows:
            result = ExperimentItemResult(item_id=item.id, position=run.position, input=item.input, expected=item.expected,
                                          status=run.status, error=run.error, session_id=run.session_id, run_id=run.run_id)
            if run.session_id and run.run_id and run.status in ("done", "needs_approval", "timed_out"):
                try:
                    trace = await self._traces.run(identity=identity, user_id=user_id, session_id=run.session_id,
                                                   run_id=run.run_id, db=db)
                except (TraceNotFoundError, HTTPException):
                    trace = None
                if trace is not None:
                    r: dict[str, Any] = trace.rollup
                    result.run_status = r.get("status")
                    result.summary = trace.summary
                    result.cost = r.get("cost")
                    result.duration_ms = r.get("duration_ms")
                    result.verdicts = [
                        Verdict(name=s.name, source=s.source, label=s.label, reason=s.reason)
                        for s in trace.scores
                        if s.source in ("check", "judge")
                    ]
            out.append(result)
        return out

    @staticmethod
    def _stats(results: list[ExperimentItemResult]) -> ExperimentStats:
        finished = [r for r in results if r.status in ("done", "needs_approval", "timed_out", "failed")]
        costs = [r.cost for r in results if r.cost is not None]
        times = [r.duration_ms for r in results if r.duration_ms is not None]
        return ExperimentStats(
            items=len(results),
            finished=len(finished),
            answered=sum(1 for r in results if r.run_status == "ok"),
            failed=sum(1 for r in results if r.status in ("failed", "timed_out") or r.run_status in ("error", "budget_exceeded", "interrupted")),
            needs_approval=sum(1 for r in results if r.status == "needs_approval"),
            checks_failed=sum(1 for r in results if any(v.source == "check" and v.label == "fail" for v in r.verdicts)),
            judge_pass=sum(1 for r in results for v in r.verdicts if v.source == "judge" and v.label == "pass"),
            judge_fail=sum(1 for r in results for v in r.verdicts if v.source == "judge" and v.label == "fail"),
            avg_cost=round(sum(costs) / len(costs), 6) if costs else None,
            avg_duration_ms=round(sum(times) / len(times)) if times else None,
        )

    def _exp_out(self, exp: Experiment, results: list[ExperimentItemResult]) -> ExperimentOut:
        return ExperimentOut(
            id=exp.id, dataset_id=exp.dataset_id, label=exp.label, release_version=exp.release_version,
            composition_digest=exp.composition_digest, status=exp.status, error=exp.error, created_at=exp.created_at,
            started_at=exp.started_at, finished_at=exp.finished_at, stats=self._stats(results) if results else None,
        )

    async def experiment(
        self, *, identity: TenantPrincipal, user_id: str, experiment_id: str, db: AsyncSession
    ) -> ExperimentDetail:
        exp = await self._experiment(db, user_id, experiment_id)
        await self._settle_stale(exp, db)
        dataset = await self._dataset(db, user_id, exp.dataset_id)
        results = await self._results(identity, user_id, exp, db)
        return ExperimentDetail(experiment=self._exp_out(exp, results), dataset_name=dataset.name, results=results)

    @staticmethod
    def _score(r: ExperimentItemResult | None) -> int | None:
        """How well one replay went, to compare two: higher is better; None if it did not finish."""
        if r is None or r.status == "pending" or r.status == "running":
            return None
        if r.status in ("failed", "timed_out", "needs_approval") or r.run_status != "ok":
            return 0
        fails = sum(1 for v in r.verdicts if v.label == "fail")
        return 10 - min(fails, 9)

    async def compare(
        self, *, identity: TenantPrincipal, user_id: str, a_id: str, b_id: str, db: AsyncSession
    ) -> ExperimentComparison:
        a = await self.experiment(identity=identity, user_id=user_id, experiment_id=a_id, db=db)
        b = await self.experiment(identity=identity, user_id=user_id, experiment_id=b_id, db=db)
        if a.experiment.dataset_id != b.experiment.dataset_id:
            raise HTTPException(status_code=422, detail={"error_code": "DIFFERENT_DATASETS",
                                                         "message": "compare experiments of the same dataset"})
        a_by = {r.item_id: r for r in a.results}
        b_by = {r.item_id: r for r in b.results}
        rows: list[ComparisonRow] = []
        for item_id in list(dict.fromkeys([*a_by, *b_by])):
            ra, rb = a_by.get(item_id), b_by.get(item_id)
            sa, sb = self._score(ra), self._score(rb)
            change = "unknown" if sa is None or sb is None else "better" if sb > sa else "worse" if sb < sa else "same"
            either = ra or rb
            rows.append(ComparisonRow(item_id=item_id, input=either.input if either else "", a=ra, b=rb, change=change))
        return ExperimentComparison(
            a=a.experiment, b=b.experiment, rows=rows,
            better=sum(1 for r in rows if r.change == "better"), worse=sum(1 for r in rows if r.change == "worse"),
        )
