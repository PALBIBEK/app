"""Proposing a change by itself: when an agent's biggest issue keeps growing, start a proposal.

Prime Agent's auto-refine (``crates/pa-core/src/session_engine/auto_refine_trigger.rs``,
``consume_compact_auto_refine``) is the design followed, gate by gate:

1. **On.** Only agents whose owner turned it on (``AutoImprove.enabled``).
2. **Not while one runs.** A proposal of the agent's still being worked on owns the round
   (PA's ``in_flight``): the check waits, without stamping anything.
3. **Cooldown.** At most one attempt per ``cooldown_minutes``; *every* attempt stamps it,
   whatever it came to ("every attempt stamps the cooldown window"). PA's default is 20
   minutes for a model review; an attempt here replays a dataset, so the default is 6 hours.
4. **Enough evidence.** PA refines after ``turn_interval`` settled turns since the last
   review; here, after ``min_runs`` failing runs of one issue since the last attempt (live
   traffic only: an experiment's replays are measurements, not failures).

Then the round itself: that issue's runs are saved to the agent's chosen dataset (what the
change is measured on: the failures it targets), and a proposal is started as a person's would
be. It still goes through the gate, and nothing goes live unless a person approves it.

The check runs every ``auto_improve_interval_s`` in each API process; a Postgres advisory lock
(as ``prime_agent.db.migrate`` takes for migrations) lets one replica check at a time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import AutoImprove, Dataset, Proposal
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.user import User
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.evaluation import AutoImproveIn, AutoImproveOut
from prime_agent.services.ai_client import AiClient
from prime_agent.services.evaluation_service import EvaluationNotFoundError, EvaluationService, SessionFactory
from prime_agent.services.improvement_service import ACTIVE, ImprovementNotFoundError, ImprovementService
from prime_agent.services.trace_service import TraceService

__all__ = ["AutoImprover", "Start"]

logger = logging.getLogger(__name__)

_LOCK_KEY: Final = 0x0B4C_A171  # pg_try_advisory_lock key for "auto-improve check"

Start = Callable[[ImprovementService, str, TenantPrincipal], None]
"""Runs a started proposal in the background (``web.routes.evaluations.start_improving``)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=UTC) if at is not None and at.tzinfo is None else at


def _out(row: AutoImprove) -> AutoImproveOut:
    return AutoImproveOut(
        agent_id=row.harness_id, dataset_id=row.dataset_id, enabled=row.enabled, min_runs=row.min_runs,
        cooldown_minutes=row.cooldown_minutes, rounds=row.rounds, last_attempt_at=_aware(row.last_attempt_at),
        last_proposal_id=row.last_proposal_id, last_note=row.last_note,
    )


class AutoImprover:
    def __init__(self, settings: Settings, *, client: AiClient | None = None) -> None:
        self._settings = settings
        self._client = client or AiClient.from_settings(settings)
        self._traces = TraceService(settings, client=self._client)
        self._evals = EvaluationService(settings, client=self._client)
        self._improver = ImprovementService(settings, client=self._client)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AutoImprover:
        return cls(settings or get_settings())

    # --- the owner's setting ----------------------------------------------------------

    async def get(self, *, user_id: str, agent_id: str, db: AsyncSession) -> AutoImproveOut | None:
        await self._agent(db, user_id, agent_id)
        row = await db.get(AutoImprove, agent_id)
        return _out(row) if row is not None else None

    async def save(self, *, user_id: str, agent_id: str, body: AutoImproveIn, db: AsyncSession) -> AutoImproveOut:
        await self._agent(db, user_id, agent_id)
        dataset = await db.get(Dataset, body.dataset_id)
        if dataset is None or dataset.user_id != user_id or dataset.harness_id != agent_id:
            raise EvaluationNotFoundError()
        row = await db.get(AutoImprove, agent_id)
        if row is None:
            row = AutoImprove(harness_id=agent_id, user_id=user_id, dataset_id=body.dataset_id)
            db.add(row)
        row.dataset_id, row.enabled, row.min_runs = body.dataset_id, body.enabled, body.min_runs
        row.cooldown_minutes, row.rounds = body.cooldown_minutes, body.rounds
        await db.commit()
        await db.refresh(row)
        return _out(row)

    @staticmethod
    async def _agent(db: AsyncSession, user_id: str, agent_id: str) -> Harness:
        agent = (
            await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
        ).scalar_one_or_none()
        if agent is None:
            raise EvaluationNotFoundError()
        return agent

    # --- the check ----------------------------------------------------------------------

    async def tick(self, session_factory: SessionFactory, start: Start) -> list[str]:
        """Check every agent that proposes by itself; the ids of the proposals started. Another
        replica checking already: nothing (it holds the lock)."""
        async with session_factory() as lock:
            postgres = (await lock.connection()).dialect.name == "postgresql"
            if postgres and not await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}):
                return []
            try:
                async with session_factory() as db:
                    agents = list((await db.execute(select(AutoImprove.harness_id).where(AutoImprove.enabled.is_(True)))).scalars())
                started: list[str] = []
                for agent_id in agents:
                    try:
                        async with session_factory() as db:
                            if (proposal_id := await self._check(db, agent_id, start)) is not None:
                                started.append(proposal_id)
                    except Exception:  # one agent's failure never stops the others' checks
                        logger.exception("auto-improve check of agent %s failed", agent_id)
                return started
            finally:
                if postgres:
                    await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})

    async def _check(self, db: AsyncSession, agent_id: str, start: Start) -> str | None:
        row = await db.get(AutoImprove, agent_id)
        user = await db.get(User, row.user_id) if row is not None else None
        if row is None or user is None or not row.enabled:
            return None
        busy = (
            await db.execute(select(Proposal.id).where(Proposal.harness_id == agent_id, Proposal.status.in_(ACTIVE)))
        ).first()
        if busy is not None:  # gate 2: one round at a time; nothing stamped
            return None
        now = _now()
        last = _aware(row.last_attempt_at)
        if last is not None and now - last < timedelta(minutes=row.cooldown_minutes):
            return None  # gate 3
        since = last or _aware(row.created_at) or now
        identity = TenantPrincipal.from_user(user)
        found = await self._traces.issues(identity=identity, user_id=user.id, agent_id=agent_id,
                                          since_ms=int(since.timestamp() * 1000), db=db, live_only=True)
        top = max(found.issues, key=lambda i: i.runs, default=None)
        if top is None or top.runs < row.min_runs:  # gate 4: not enough yet; nothing stamped
            row.last_note = (
                f"“{top.title}” failed {top.runs} run{'s' if top.runs != 1 else ''} since the last attempt; "
                f"a change is proposed at {row.min_runs}" if top else "no failing runs since the last attempt"
            )
            await db.commit()
            return None

        row.last_attempt_at = now  # every attempt stamps the cooldown, whatever it comes to
        note = f"“{top.title}” failed {top.runs} runs since {since:%Y-%m-%d %H:%M} UTC"
        try:
            saved = await self._evals.add_issue(identity=identity, user_id=user.id, dataset_id=row.dataset_id,
                                                issue_key=top.key, db=db)
            added = len(saved.added)
        except HTTPException as exc:  # a full dataset still measures the change on what it holds
            if exc.status_code != 409:
                raise
            added = 0
        try:
            proposal = await self._improver.propose(
                user_id=user.id, agent_id=agent_id, dataset_id=row.dataset_id, rounds=row.rounds, db=db,
                notes=f"Started automatically: {note}. Fix what makes these runs fail.",
            )
        except (HTTPException, ImprovementNotFoundError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "the agent or dataset is gone"
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
            row = await db.get(AutoImprove, agent_id) or row
            row.last_attempt_at, row.last_note = now, f"{note}; could not start a change: {message}"
            await db.commit()
            return None
        row = await db.get(AutoImprove, agent_id) or row
        row.last_attempt_at, row.last_proposal_id = now, proposal.id
        row.last_note = f"{note}: started a change" + (f", {added} of its runs saved to the dataset" if added else "")
        await db.commit()
        start(self._improver, proposal.id, identity)
        return proposal.id

    async def run_forever(self, session_factory: SessionFactory, start: Start) -> None:
        """Check every ``auto_improve_interval_s`` until cancelled (the app's lifespan)."""
        interval = self._settings.auto_improve_interval_s
        while interval > 0:
            await asyncio.sleep(interval)
            try:
                await self.tick(session_factory, start)
            except Exception:
                logger.exception("auto-improve check failed")
