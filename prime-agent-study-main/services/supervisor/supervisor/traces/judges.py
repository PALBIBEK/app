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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionRow, TraceJudgeRow, TraceScoreRow
from supervisor.seams.core.types import ChatMessage
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.traces.model import RunTrace
from supervisor.traces.render import run_text

__all__ = [
    "JUDGE_SYSTEM",
    "Judge",
    "Verdict",
    "delete_judge",
    "judge_messages",
    "judge_out",
    "judge_run",
    "list_judges",
    "parse_verdict",
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


def _ask(client: ModelGatewayClient, judge: Judge, trace: RunTrace) -> str:
    completion = client.complete(judge_messages(judge, trace), [], model=judge.model, tool_choice="none")
    return str(completion.message.content or "")


async def judge_run(
    factory: Factory, session: SessionRow, trace: RunTrace, client: ModelGatewayClient | None
) -> list[Verdict]:
    """Apply the agent's enabled judges to a finished run (each on its sample); returns the
    verdicts given. A judge that cannot be asked (no client, the call failed) gives none."""
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
    given: list[Verdict] = []
    for judge in judges:
        if not sampled(judge, trace.run_id):
            continue
        try:
            reply = await asyncio.to_thread(_ask, client, judge, trace)
        except Exception:
            logger.warning("judge %s could not be asked about run %s", judge.name, trace.run_id, exc_info=True)
            continue
        verdict = parse_verdict(reply)
        async with factory() as db, db.begin():
            await db.execute(
                delete(TraceScoreRow).where(
                    TraceScoreRow.session_id == trace.session_id,
                    TraceScoreRow.run_id == trace.run_id,
                    TraceScoreRow.source == "judge",
                    TraceScoreRow.name == judge.name,
                )
            )
            db.add(
                TraceScoreRow(
                    id=str(uuid4()), tenant_id=session.tenant_id, session_id=trace.session_id, run_id=trace.run_id,
                    span_id=None, name=judge.name, topic=judge.name, source="judge", label=verdict.label,
                    value={"pass": 1.0, "fail": 0.0}.get(verdict.label), reason=verdict.reason, comment=None,
                    evaluator=judge.evaluator, principal_id=None, composition_id=composition_id,
                    composition_digest=pins.get("composition_digest"), created_ms=int(time.time() * 1000),
                )
            )
        given.append(verdict)
    return given
