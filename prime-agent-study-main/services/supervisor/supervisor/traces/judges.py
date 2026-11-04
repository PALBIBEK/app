"""Judges: a rubric a model applies to an agent's finished runs.

A person writes what a good run looks like ("answers the question asked; never edits files
it was not asked to"). After a run ends, each enabled judge of its agent reads the run as
text (``render.run_text``) on a sampled share of runs, and answers pass or fail with a
reason. The verdict is a score like a check's, so it shows on the run, filters lists and
groups into issues.

The judge calls the model through the session's own gateway client (its key, its budget):
judging costs what the agent's own calls cost, and is opt-in per agent. A reply that is not
a verdict is recorded as ``unclear``, never guessed. Sampling is deterministic per run and
judge, so re-judging a run never flips whether it is judged.

A judge is only as good as its agreement with people. ``agreement`` compares each judge's
verdicts with people's good/bad on the same runs (agreement and Cohen's kappa, as
``sklearn.metrics.cohen_kappa_score``), and ``preview`` applies a rubric to recent runs
without keeping anything, so a rubric is checked before it grades anything (EvalGen,
arXiv 2404.12272: criteria are refined by grading outputs). ``TRACE_JUDGE_MODEL`` sets the
model a judge with none of its own uses, so the agent does not grade itself by default.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionEvaluationRow, SessionRow, TraceJudgeRow, TraceScoreRow
from supervisor.obs.evaluations import emit_evaluation
from supervisor.seams.core.types import ChatMessage
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.session.access import session_visibility
from supervisor.traces.model import RunTrace
from supervisor.traces.render import run_text

__all__ = [
    "JUDGE_MODEL_ENV",
    "JUDGE_SYSTEM",
    "REFERENCE_JUDGE",
    "Judge",
    "Verdict",
    "agreement",
    "delete_judge",
    "judge_messages",
    "judge_out",
    "judge_run",
    "kappa",
    "list_judges",
    "parse_verdict",
    "preview",
    "sampled",
    "save_judge",
]

logger = logging.getLogger(__name__)
Factory = async_sessionmaker[AsyncSession]

JUDGE_SYSTEM = (
    "You judge one run of an AI agent against a rubric. Read the run, then decide whether it "
    "meets the rubric. Judge only what the run shows; if it does not show enough, fail it and "
    'say what is missing. Answer with JSON only: {"verdict": "pass" or "fail", "reason": '
    '"one sentence a person can act on"}.'
)


JUDGE_MODEL_ENV = "TRACE_JUDGE_MODEL"
"""The model a judge without one uses, when set; otherwise the session's own model."""
PREVIEW_RUNS = 5
"""Recent runs a rubric is tried on before it is saved."""

REFERENCE_JUDGE = "good answer"
"""The built-in judge of a replay whose saved run has a good answer: does the run meet it."""
REFERENCE_EVALUATOR = "reference:closed_qa@v1"
REFERENCE_MODEL_ENV = "TRACE_REFERENCE_MODEL"
"""The model that grades against a good answer, when set; otherwise the session's own."""

# Braintrust autoevals' ClosedQA (templates/closed_q_a.yaml): the task, the submission and a
# criterion, "Does the submission meet the criterion?" A saved good answer says what a good
# answer does, which is a criterion. Asked for this module's verdict JSON, so an unusable
# reply is ``unclear`` like any judge's.
REFERENCE_SYSTEM = (
    "You are assessing a submitted answer on a given task based on a criterion. Decide whether "
    "the submission meets the criterion. Judge only what the submission shows. Answer with JSON "
    'only: {"verdict": "pass" or "fail", "reason": "one sentence a person can act on"}.'
)


def reference_messages(trace: RunTrace, criterion: str) -> list[ChatMessage]:
    data = (
        "[BEGIN DATA]\n***\n"
        f"[Task]: {trace.rollup.input or '(not recorded)'}\n***\n"
        f"[Submission]: {trace.rollup.output or '(the run gave no answer)'}\n***\n"
        f"[Criterion]: {criterion.strip()}\n***\n[END DATA]\n"
        "Does the submission meet the criterion? Your verdict as JSON:"
    )
    return [ChatMessage(role="system", content=REFERENCE_SYSTEM), ChatMessage(role="user", content=data)]


@dataclass(frozen=True)
class Judge:
    id: str
    composition_id: str
    name: str
    rubric: str
    sample_rate: float
    model: str | None
    version: int
    enabled: bool

    @property
    def evaluator(self) -> str:
        return f"judge:{self.name}@v{self.version}"


@dataclass(frozen=True)
class Verdict:
    label: str
    """``pass``, ``fail``, or ``unclear`` (the reply was not a verdict)."""
    reason: str


def _judge(row: TraceJudgeRow) -> Judge:
    return Judge(row.id, row.composition_id, row.name, row.rubric, row.sample_rate, row.model, row.version,
                 row.enabled)


def judge_out(row: TraceJudgeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "composition_id": row.composition_id,
        "name": row.name,
        "rubric": row.rubric,
        "sample_rate": row.sample_rate,
        "model": row.model,
        "version": row.version,
        "enabled": row.enabled,
        "created_ms": row.created_ms,
        "updated_ms": row.updated_ms,
    }


def sampled(judge: Judge, run_id: str) -> bool:
    """Whether ``judge`` judges this run: the same answer every time for the same pair."""
    if judge.sample_rate >= 1:
        return True
    if judge.sample_rate <= 0:
        return False
    h = int(hashlib.sha256(f"{judge.id}:{run_id}".encode()).hexdigest()[:8], 16)
    return h / 0xFFFFFFFF < judge.sample_rate


def judge_messages(judge: Judge, trace: RunTrace) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=JUDGE_SYSTEM),
        ChatMessage(
            role="user",
            content=f"Rubric:\n{judge.rubric.strip()}\n\nThe run:\n{run_text(trace)}\n\nYour verdict as JSON:",
        ),
    ]


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(reply: str) -> Verdict:
    """The judge's verdict; ``unclear`` when the reply is not one (never a guess)."""
    found = _JSON.search(reply or "")
    if found:
        try:
            data = json.loads(found.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            verdict = str(data.get("verdict", "")).strip().lower()
            reason = str(data.get("reason", "")).strip()[:500] or "no reason given"
            if verdict in ("pass", "fail"):
                return Verdict(verdict, reason)
    head = " ".join((reply or "").split())[:120]
    return Verdict("unclear", f"the judge's reply was not a verdict: {head}" if head else "the judge did not reply")


async def list_judges(factory: Factory, *, tenant_id: str, composition_id: str) -> list[dict[str, Any]]:
    async with factory() as db:
        rows = (
            await db.execute(
                select(TraceJudgeRow)
                .where(TraceJudgeRow.tenant_id == tenant_id, TraceJudgeRow.composition_id == composition_id)
                .order_by(TraceJudgeRow.name.asc())
            )
        ).scalars().all()
    return [judge_out(r) for r in rows]


async def save_judge(
    factory: Factory,
    *,
    tenant_id: str,
    composition_id: str,
    name: str,
    rubric: str,
    sample_rate: float,
    model: str | None,
    enabled: bool,
    created_by: str | None,
) -> dict[str, Any]:
    """Create the judge, or change it: a new rubric or model is a new version."""
    now = int(time.time() * 1000)
    async with factory() as db, db.begin():
        row = (
            await db.execute(
                select(TraceJudgeRow).where(
                    TraceJudgeRow.tenant_id == tenant_id,
                    TraceJudgeRow.composition_id == composition_id,
                    TraceJudgeRow.name == name,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = TraceJudgeRow(
                id=str(uuid4()), tenant_id=tenant_id, composition_id=composition_id, name=name, rubric=rubric,
                sample_rate=sample_rate, model=model, version=1, enabled=enabled, created_by=created_by,
                created_ms=now, updated_ms=now,
            )
            db.add(row)
        else:
            if row.rubric != rubric or row.model != model:
                row.version += 1
            row.rubric, row.model, row.sample_rate, row.enabled, row.updated_ms = rubric, model, sample_rate, enabled, now
        await db.flush()
        return judge_out(row)


async def delete_judge(factory: Factory, *, tenant_id: str, judge_id: str) -> bool:
    """Remove a judge (its past verdicts stay, under the version that gave them)."""
    async with factory() as db, db.begin():
        gone = await db.execute(
            delete(TraceJudgeRow).where(TraceJudgeRow.id == judge_id, TraceJudgeRow.tenant_id == tenant_id)
        )
        return bool(getattr(gone, "rowcount", 0))


def _complete(client: ModelGatewayClient, messages: list[ChatMessage], model: str | None) -> str:
    completion = client.complete(messages, [], model=model, tool_choice="none")
    return str(completion.message.content or "")


async def judge_run(
    factory: Factory, session: SessionRow, trace: RunTrace, client: ModelGatewayClient | None
) -> list[Verdict]:
    """Apply the agent's enabled judges to a finished run (each on its sample; a replay for an
    experiment by all of them); returns the verdicts given. A judge that cannot be asked (no
    client, the call failed) gives none."""
    pins = session.composition_pins if isinstance(session.composition_pins, dict) else {}
    composition_id = pins.get("composition_id")
    if client is None or not composition_id or not trace.rollup.status.finished:
        return []
    async with factory() as db:
        judges = [
            _judge(r)
            for r in (
                await db.execute(
                    select(TraceJudgeRow).where(
                        TraceJudgeRow.tenant_id == session.tenant_id,
                        TraceJudgeRow.composition_id == composition_id,
                        TraceJudgeRow.enabled.is_(True),
                    )
                )
            ).scalars()
        ]
        replay = await db.get(SessionEvaluationRow, session.id)
    default = os.environ.get(JUDGE_MODEL_ENV, "").strip() or None
    asks: list[tuple[str, str, str | None, list[ChatMessage]]] = [
        (judge.name, judge.evaluator, judge.model or default, judge_messages(judge, trace))
        for judge in judges
        if replay is not None or sampled(judge, trace.run_id)
    ]
    if replay is not None and replay.expected:
        model = os.environ.get(REFERENCE_MODEL_ENV, "").strip() or None
        asks.append((REFERENCE_JUDGE, REFERENCE_EVALUATOR, model, reference_messages(trace, replay.expected)))
    given: list[Verdict] = []
    for name, evaluator, model, messages in asks:
        try:
            reply = await asyncio.to_thread(_complete, client, messages, model)
        except Exception:
            logger.warning("judge %s could not be asked about run %s", name, trace.run_id, exc_info=True)
            continue
        verdict = parse_verdict(reply)
        async with factory() as db, db.begin():
            await db.execute(
                delete(TraceScoreRow).where(
                    TraceScoreRow.session_id == trace.session_id,
                    TraceScoreRow.run_id == trace.run_id,
                    TraceScoreRow.source == "judge",
                    TraceScoreRow.name == name,
                )
            )
            db.add(
                TraceScoreRow(
                    id=str(uuid4()), tenant_id=session.tenant_id, session_id=trace.session_id, run_id=trace.run_id,
                    span_id=None, name=name, topic=name, source="judge", label=verdict.label,
                    value={"pass": 1.0, "fail": 0.0}.get(verdict.label), reason=verdict.reason, comment=None,
                    evaluator=evaluator, principal_id=None, composition_id=composition_id,
                    composition_digest=pins.get("composition_digest"), created_ms=int(time.time() * 1000),
                )
            )
        emit_evaluation(session_id=trace.session_id, run_id=trace.run_id, name=name, label=verdict.label,
                        value={"pass": 1.0, "fail": 0.0}.get(verdict.label), explanation=verdict.reason,
                        source="judge", evaluator=evaluator)
        given.append(verdict)
    return given


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa of two raters' pass/fail on the same runs: agreement beyond what chance
    gives, as ``sklearn.metrics.cohen_kappa_score`` (unweighted): ``1 - Σ w·observed / Σ w·expected``
    with ``expected`` the outer product of the marginals over the total. None when it is
    undefined (no pairs, or chance already explains everything)."""
    n = len(pairs)
    if not n:
        return None
    observed = {(a, b): sum(1 for p in pairs if p == (a, b)) for a in (True, False) for b in (True, False)}
    first = {a: observed[(a, True)] + observed[(a, False)] for a in (True, False)}
    second = {b: observed[(True, b)] + observed[(False, b)] for b in (True, False)}
    disagree = observed[(True, False)] + observed[(False, True)]
    chance = (first[True] * second[False] + first[False] * second[True]) / n
    if chance == 0:
        return None
    return round(1 - disagree / chance, 4)


async def agreement(
    factory: Factory, *, tenant_id: str, principal_id: str | None, composition_id: str
) -> list[dict[str, Any]]:
    """For each judge of the composition: on the runs people marked good or bad, how often its
    pass/fail matched, Cohen's kappa, and the two ways it was wrong (it passed a run people
    marked bad; it failed one they marked good). An experiment's replays are left out."""
    async with factory() as db:
        rows = (
            await db.execute(
                select(TraceScoreRow.session_id, TraceScoreRow.run_id, TraceScoreRow.source, TraceScoreRow.name,
                       TraceScoreRow.label)
                .join(SessionRow, SessionRow.id == TraceScoreRow.session_id)
                .where(SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id),
                       TraceScoreRow.composition_id == composition_id,
                       TraceScoreRow.source.in_(("judge", "person")),
                       ~exists().where(SessionEvaluationRow.session_id == TraceScoreRow.session_id))
            )
        ).all()
    people: dict[tuple[str, str], bool] = {}
    judged: dict[str, dict[tuple[str, str], bool]] = {}
    for session_id, run_id, source, name, label in rows:
        run = (session_id, run_id)
        if source == "person" and label in ("good", "bad"):
            people[run] = label == "good"
        elif source == "judge" and label in ("pass", "fail"):
            judged.setdefault(name, {})[run] = label == "pass"
    out: list[dict[str, Any]] = []
    for name in sorted(judged):
        pairs = [(verdict, people[run]) for run, verdict in judged[name].items() if run in people]
        agree = sum(1 for j, p in pairs if j == p)
        out.append({
            "name": name,
            "runs": len(pairs),
            "agreement": round(agree / len(pairs), 4) if pairs else None,
            "kappa": kappa(pairs),
            "passed_but_marked_bad": sum(1 for j, p in pairs if j and not p),
            "failed_but_marked_good": sum(1 for j, p in pairs if not j and p),
        })
    return out


async def preview(
    rubric: str, traces: list[RunTrace], client: ModelGatewayClient, model: str | None
) -> list[tuple[RunTrace, Verdict]]:
    """``rubric`` applied to ``traces`` as a judge would, keeping nothing: what it would say."""
    judge = Judge("preview", "", "preview", rubric, 1.0, model, 0, True)
    out: list[tuple[RunTrace, Verdict]] = []
    for trace in traces:
        try:
            reply = await asyncio.to_thread(_complete, client, judge_messages(judge, trace),
                                            model or os.environ.get(JUDGE_MODEL_ENV, "").strip() or None)
        except Exception as exc:  # noqa: BLE001 - said in the verdict, as a judge that could not be asked
            out.append((trace, Verdict("unclear", f"the judge could not be asked: {exc}"[:300])))
            continue
        out.append((trace, parse_verdict(reply)))
    return out
