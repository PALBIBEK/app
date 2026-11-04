"""Watching an approved change live, and rolling it back when it does worse than before.

A change passes its gate on saved runs; live traffic is wider. After approval its version is
watched against the version it replaced, as Flagger analyses a canary
(``fluxcd/flagger`` ``pkg/controller/scheduler.go``):

- each check needs new live runs of the version (Flagger skips the analysis while no traffic
  reaches the canary): ``watch_min_runs`` more than at the last check;
- a check fails when the share of the version's live runs that failed or failed a check, or
  that people marked wrong, is more than ``watch_margin`` above the previous version's (a
  metric outside its threshold range); a failed check adds one to ``failed_checks``
  (``SetStatusFailedChecks(cd, cd.Status.FailedChecks+1)``);
- at ``watch_threshold`` failed checks it is rolled back
  (``cd.Status.FailedChecks >= cd.GetAnalysisThreshold()`` then ``c.rollback``): the previous
  release is made live again as the next version, its exact artifacts (nothing rebuilt);
- after ``watch_checks`` passed checks the watch ends: the change is held.

A person releasing another version meanwhile ends the watch (superseded): the watcher never
undoes a person's newer release. Live runs only: an experiment's replays are measurements.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Proposal
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.user import User
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient
from prime_agent.services.evaluation_service import SessionFactory
from prime_agent.services.harness_publish_service import HarnessPublisher, releases

__all__ = ["ReleaseWatcher", "share_of_bad_runs"]

logger = logging.getLogger(__name__)

_LOCK_KEY: Final = 0x0B4C_3A7C  # pg_try_advisory_lock key for "release watch"


def share_of_bad_runs(quality: dict[str, Any]) -> tuple[float, float]:
    """A version's live runs: the share that failed or failed a check, and the share people
    marked wrong. Zero runs: (0, 0)."""
    runs = int(quality.get("runs") or 0)
    if not runs:
        return 0.0, 0.0
    failing = int(quality.get("failed") or 0) + int(quality.get("runs_failing_checks") or 0)
    return min(failing / runs, 1.0), min(int(quality.get("feedback_bad") or 0) / runs, 1.0)


class ReleaseWatcher:
    def __init__(self, settings: Settings, *, client: AiClient | None = None) -> None:
        self._settings = settings
        self._client = client or AiClient.from_settings(settings)
        self._publisher = HarnessPublisher(self._client)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ReleaseWatcher:
        return cls(settings or get_settings())

    async def tick(self, session_factory: SessionFactory) -> list[str]:
        """One check of every watched change; the ids of those rolled back."""
        async with session_factory() as lock:
            postgres = (await lock.connection()).dialect.name == "postgresql"
            if postgres and not await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}):
                return []
            try:
                async with session_factory() as db:
                    watched = [
                        p.id for p in (await db.execute(select(Proposal).where(Proposal.status == "approved"))).scalars()
                        if (p.watch or {}).get("status") == "watching"
                    ]
                rolled: list[str] = []
                for proposal_id in watched:
                    try:
                        async with session_factory() as db:
                            if await self._check(db, proposal_id):
                                rolled.append(proposal_id)
                    except Exception:  # one change's failure never stops the others' checks
                        logger.exception("release watch of proposal %s failed", proposal_id)
                return rolled
            finally:
                if postgres:
                    await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})

    async def _check(self, db: AsyncSession, proposal_id: str) -> bool:
        """Check one change; True when it was rolled back."""
        row = await db.get(Proposal, proposal_id)
        if row is None:
            return False
        watch = dict(row.watch or {})
        agent = await db.get(Harness, row.harness_id)
        user = await db.get(User, row.user_id)
        history = await releases(row.harness_id, db) if agent is not None else []
        ours = next((r for r in history if r.version == row.release_version), None)
        before = next((r for r in history if r.version == watch.get("previous_version")), None)
        if agent is None or user is None or ours is None or before is None:
            return await self._end(db, row, watch, "ended", "its version or the one before it is gone")
        if history[0].version != ours.version:
            return await self._end(db, row, watch, "superseded", f"v{history[0].version} was released after it")

        raw = await self._client.trace_versions(TenantPrincipal.from_user(user), ours.composition_id, live_only=True)
        by_digest = {v.get("composition_digest"): v for v in raw.get("versions") or [] if isinstance(v, dict)}
        now, then = by_digest.get(ours.composition_digest, {}), by_digest.get(before.composition_digest, {})
        runs = int(now.get("runs") or 0)
        if runs - int(watch.get("seen_runs") or 0) < self._settings.watch_min_runs:
            return False  # not enough new live runs for a check: wait (Flagger: no traffic, no analysis)

        (failing, bad), (failing_before, bad_before) = share_of_bad_runs(now), share_of_bad_runs(then)
        margin = self._settings.watch_margin
        worse: list[str] = []
        if failing > failing_before + margin:
            worse.append(f"{failing:.0%} of its live runs failed, against {failing_before:.0%} on v{before.version}")
        if bad > bad_before + margin:
            worse.append(f"people marked {bad:.0%} of its live runs wrong, against {bad_before:.0%} on v{before.version}")
        watch["seen_runs"] = runs
        if not worse:
            watch["checks"] = int(watch.get("checks") or 0) + 1
            if watch["checks"] >= self._settings.watch_checks:
                return await self._end(db, row, watch, "held",
                                       f"no worse than v{before.version} over {runs} live runs")
            watch["note"] = f"check {watch['checks']} of {self._settings.watch_checks} passed ({runs} live runs)"
            row.watch = watch
            await db.commit()
            return False
        watch["failed_checks"] = int(watch.get("failed_checks") or 0) + 1
        if watch["failed_checks"] < self._settings.watch_threshold:
            watch["note"] = f"a check failed: {'; '.join(worse)}"
            row.watch = watch
            await db.commit()
            return False
        restored = await self._publisher.restore(agent, before, user_id=row.user_id, db=db)
        watch["rolled_back_to"] = restored.release.version
        return await self._end(db, row, watch, "rolled_back",
                               f"{'; '.join(worse)}: v{before.version} is live again as v{restored.release.version}")

    @staticmethod
    async def _end(db: AsyncSession, row: Proposal, watch: dict[str, Any], status: str, note: str) -> bool:
        row = await db.get(Proposal, row.id) or row
        row.watch = {**watch, "status": status, "note": note}
        await db.commit()
        return status == "rolled_back"

    async def run_forever(self, session_factory: SessionFactory) -> None:
        """Check every ``watch_interval_s`` until cancelled (the app's lifespan)."""
        interval = self._settings.watch_interval_s
        while interval > 0:
            await asyncio.sleep(interval)
            try:
                await self.tick(session_factory)
            except Exception:
                logger.exception("release watch failed")
