"""Keeping sessions healthy over time: idle reaping, deploy drain, workspace refresh."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from supervisor.agent.events import HandStopped
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.hands.keys import split_key
from supervisor.sandbox.base import Sandbox, SnapshotUnsupported
from supervisor.seams.services import (
    WorkspaceConflictError,
)
from supervisor.session.errors import (
    WorkspaceUnavailable,
)
from supervisor.session.modes import SessionMode

logger = logging.getLogger(__name__)






from supervisor.session.manager_children import ChildOps


class UpkeepOps(ChildOps):
    """Keeping sessions healthy over time: idle reaping, idle sandboxes, deploy drain,
    workspace refresh."""

    # --- sandbox lifecycle (supervisor.hands.lifecycle) ---------------------------------

    def prewarm(self, session_id: str) -> None:
        """A run is beginning: start building the session's sandbox (each hand set to start
        ``with_run``) so the build overlaps the first model call. Never waits, never raises."""
        for key in self._hands.keys_of(session_id):
            if self.sandbox_lifecycle.starts_with_run(self._hands.recipe(key)):
                self._hands.prewarm(key)

    async def stop_idle_sandboxes(self, *, now: float | None = None) -> list[str]:
        """Save and stop every sandbox idle past its limit; returns the hands stopped.

        Skipped (and looked at again next sweep): a sandbox a tool is using, one lent to
        another job, one with processes the agent left running (a server would be lost),
        and one whose save failed: nothing is stopped that its next sandbox cannot restore.
        """
        if self._checkpoints is None:
            return []  # without a store to save to, a stopped sandbox would lose its files
        stopped: list[str] = []
        for key in list(self._hands.live):
            limit = self.sandbox_lifecycle.idle_limit(self._hands.recipe(key))
            idle = self._hands.idle_for(key, now)
            if limit <= 0 or idle < limit:
                continue
            if self._checkpoints.processes(key):
                continue
            try:
                if await self._leases.current(key) is not None:
                    continue
            except Exception:
                logger.warning("checking a hand's lease failed hand=%s", key, exc_info=True)
                continue
            sandbox = self._hands.sandbox(key)
            if sandbox is None:
                continue
            try:
                if self._checkpoints.due(key) is not None:
                    await self._ensure_checkpoint_scope(key)
                    await self._checkpoints.checkpoint(key, sandbox)
            except Exception:
                logger.exception("saving an idle sandbox failed; it keeps running hand=%s", key)
                continue
            if self._hands.in_use(key) or self._hands.sandbox(key) is not sandbox:
                continue  # a tool started while it was being saved
            self._hands.stop(key)
            try:
                await sandbox.destroy()
            except Exception:
                logger.warning("destroying an idle sandbox failed hand=%s", key, exc_info=True)
            session_id, hand = split_key(key)
            await self._record(
                session_id,
                HandStopped(
                    hand_id=sandbox.id, provider=sandbox.info.provider, reason="idle",
                    idle_s=round(idle, 1), hand=hand,
                ),
                turn_id=None,
                trace_id=self._trace_for(session_id),
                event_key=f"hand:{sandbox.id}:stopped",
            )
            stopped.append(key)
        return stopped

    def reattach_sandbox(self, session_id: str, sandbox: Sandbox) -> None:
        """Bind a live sandbox discovered after supervisor restart."""
        self._hands.attach(session_id, sandbox)

    async def rehydrate_workspace(self, session_id: str) -> None:
        """Force hydrate durable SoT into the live sandbox cache (D9 LOCAL_DEMO)."""
        await self._require_active(session_id)
        ws = self._workspace.get(session_id)
        sandbox = self._hands.sandbox(session_id)
        if ws is None or ws.scope_id is None:
            raise WorkspaceUnavailable("session has no scope-bound workspace")
        if sandbox is None:
            raise WorkspaceUnavailable("session has no sandbox to rehydrate")
        await self._workspace_svc.ensure_hydrated(ws, sandbox, force=True)


    async def reap_idle_sessions(self, *, max_idle_seconds: float) -> list[str]:
        """Pause sticky or terminate stateless sessions idle longer than threshold."""
        if max_idle_seconds <= 0:
            return []
        threshold = datetime.now(UTC) - timedelta(seconds=max_idle_seconds)
        reaped: list[str] = []
        for sid in list(self._registry.all()):
            runtime = self._registry.get(sid)
            if runtime is None:
                continue
            try:
                state = await runtime.get_state()
            except Exception:
                logger.exception("reap_idle state failed session=%s", sid)
                continue
            if not state.settled:
                continue
            if not await self._is_session_idle(sid, threshold):
                continue
            await self._reap_idle_session(sid)
            reaped.append(sid)
        return reaped

    async def _is_session_idle(self, session_id: str, threshold: datetime) -> bool:
        since = await self._session_last_activity(session_id)
        if since is None:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        return since <= threshold

    async def _session_last_activity(self, session_id: str) -> datetime | None:
        async with self._db() as db:
            idx = await db.get(SessionIndexRow, session_id)
            if idx is not None and idx.last_event_at is not None:
                return idx.last_event_at
            row = await db.get(SessionRow, session_id)
            if row is None:
                return None
            return row.updated_at or row.created_at

    async def _reap_idle_session(self, session_id: str) -> None:
        mode = self._modes.get(session_id, SessionMode.STICKY)
        if mode == SessionMode.STATELESS_PER_TURN:
            await self.terminate(session_id)
            return

        runtime = self._registry.get(session_id)
        for key in self._hands.keys_of(session_id):
            if (sandbox := self._hands.sandbox(key)) is None:
                continue
            try:
                await sandbox.pause()
            except Exception:
                logger.exception("reap_idle pause failed hand=%s", key)
                try:
                    await sandbox.destroy()
                except Exception:
                    logger.exception("reap_idle destroy failed hand=%s", key)
                self._hands.detach(key)
        if runtime is not None:
            try:
                await runtime.stop()
            except Exception:
                logger.exception("reap_idle stop failed session=%s", session_id)
        self._registry.remove(session_id)
        task = self._fanout.pop(session_id, None)
        if task is not None:
            task.cancel()
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.status = "paused"
                await db.commit()

    async def drain(self, *, timeout_seconds: float = 30.0) -> None:
        """Wait for in-flight turns to settle; pause sticky, stop the rest."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            unsettled = False
            for sid in self._registry.all():
                runtime = self._registry.get(sid)
                if runtime is None:
                    continue
                try:
                    state = await runtime.get_state()
                except Exception:
                    logger.debug("drain: runtime state unavailable", exc_info=True)
                    continue
                if not state.settled:
                    unsettled = True
                    break
            if not unsettled:
                break
            await asyncio.sleep(0.05)

        for sid in list(self._modes.keys()):
            mode = self._modes.get(sid)
            runtime = self._registry.get(sid)
            for key in self._hands.keys_of(sid):
                sandbox = self._hands.sandbox(key)
                if mode != SessionMode.STICKY or sandbox is None:
                    continue
                try:
                    await sandbox.pause()
                except SnapshotUnsupported:
                    # Expected for providers without snapshots (local_process): the
                    # adopting worker starts the sandbox afresh. Not an error.
                    logger.info("drain: %s keeps no snapshot hand=%s", sandbox.info.provider, key)
                except Exception:
                    logger.exception("drain pause failed hand=%s", key)
            if runtime is not None:
                try:
                    await runtime.stop()
                except Exception:
                    logger.exception("drain stop failed session=%s", sid)
            self._registry.remove(sid)
            task = self._fanout.pop(sid, None)
            if task is not None:
                task.cancel()

        paused_ids: list[str] = []
        for sid in list(self._modes.keys()):
            if self._modes.get(sid) == SessionMode.STICKY and sid in self._hands:
                paused_ids.append(sid)

        if paused_ids:
            async with self._db() as db:
                for sid in paused_ids:
                    row = await db.get(SessionRow, sid)
                    if row is not None and row.status == "active":
                        row.status = "paused"
                await db.commit()

    async def terminate(self, session_id: str) -> None:
        # D13 T9: reap open spans via session_terminal before tearing down the session.
        try:
            import time

            from supervisor.obs.tracing import get_projector

            proj = get_projector()
            if proj is not None:
                proj.force_close_session(
                    session_id, end_ms=int(time.time() * 1000), closed_by="session_terminal"
                )
                proj.drop_session(session_id)
        except Exception:
            logger.exception("span session_terminal reap failed session=%s", session_id)

        ws_state = self._workspace.get(session_id)
        sandbox = self._hands.sandbox(session_id)
        if ws_state is not None and sandbox is not None:
            try:
                await self._workspace_svc.sync_if_dirty(ws_state, sandbox)
            except WorkspaceConflictError:
                logger.exception("workspace conflict on terminate session=%s", session_id)
            except Exception:
                logger.exception("workspace sync failed on terminate session=%s", session_id)
        if self._vk_client is not None:
            try:
                self._vk_client.revoke_session(session_id)
            except Exception:
                logger.exception("virtual key revoke failed session=%s", session_id)
        self._key_mints.pop(session_id, None)
        self._key_expiry.pop(session_id, None)
        if self._session_keys is not None:
            try:
                await self._session_keys.forget(session_id)
            except Exception:
                logger.exception("vault cleanup failed session=%s", session_id)
        runtime = self._registry.remove(session_id)
        task = self._fanout.pop(session_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if runtime is not None:
            try:
                await runtime.stop()
            except Exception:
                logger.exception("stop failed session=%s", session_id)
        for key in self._hands.keys_of(session_id) or [session_id]:
            try:
                await self._leases.take_back(key)  # a lent hand ends with its session
            except Exception:
                logger.exception("taking back a lent hand failed hand=%s", key)
            sandbox = self._hands.release(key)
            if self._checkpoints is not None:
                self._checkpoints.forget(key)
            if sandbox is not None:
                try:
                    await sandbox.destroy()
                except Exception:
                    logger.exception("sandbox destroy failed hand=%s", key)
        self._modes.pop(session_id, None)
        self._seams.pop(session_id, None)
        self._brains.pop(session_id, None)
        self._tenant_of.pop(session_id, None)
        self._long_term.pop(session_id, None)
        self._workspace.pop(session_id, None)
        self._sandbox_policy.clear(session_id)
        self._guards.clear(session_id)
        self._live.drop_session(session_id)
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.status = "terminated"
                await db.commit()
