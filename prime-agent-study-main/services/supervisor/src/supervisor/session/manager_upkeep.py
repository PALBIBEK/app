"""Keeping sessions healthy over time: idle reaping, deploy drain, workspace refresh."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.sandbox.base import Sandbox, SnapshotUnsupported
from supervisor.seams.services import (
    SessionWorkspaceState,
    WorkspaceConflictError,
)
from supervisor.session.errors import (
    WorkspaceUnavailable,
)
from supervisor.session.modes import SessionMode

logger = logging.getLogger(__name__)






from supervisor.session.manager_children import ChildOps


class UpkeepOps(ChildOps):
    """Keeping sessions healthy over time: idle reaping, deploy drain, workspace refresh."""

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

    async def _sync_workspace_fail_closed(
        self,
        session_id: str,
        ws_state: SessionWorkspaceState,
        sandbox: Sandbox,
    ) -> None:
        """Sync dirty paths; on CAS conflict rehydrate durable bytes then re-raise."""
        try:
            await self._workspace_svc.sync_if_dirty(ws_state, sandbox, force=True)
        except WorkspaceConflictError:
            await self._workspace_svc.recover_cas_conflict(ws_state, sandbox)
            raise

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

        sandbox = self._hands.sandbox(session_id)
        runtime = self._registry.get(session_id)
        if sandbox is not None:
            try:
                await sandbox.pause()
            except Exception:
                logger.exception("reap_idle pause failed session=%s", session_id)
                try:
                    await sandbox.destroy()
                except Exception:
                    logger.exception("reap_idle destroy failed session=%s", session_id)
                self._hands.detach(session_id)
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
            sandbox = self._hands.sandbox(sid)
            runtime = self._registry.get(sid)
            if mode == SessionMode.STICKY and sandbox is not None:
                try:
                    await sandbox.pause()
                except SnapshotUnsupported:
                    # Expected for providers without snapshots (local_process): the
                    # adopting worker starts the sandbox afresh. Not an error.
                    logger.info("drain: %s keeps no snapshot session=%s", sandbox.info.provider, sid)
                except Exception:
                    logger.exception("drain pause failed session=%s", sid)
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
        sandbox = self._hands.release(session_id)
        if self._checkpoints is not None:
            self._checkpoints.forget(session_id)
        if sandbox is not None:
            try:
                await sandbox.destroy()
            except Exception:
                logger.exception("sandbox destroy failed session=%s", session_id)
        self._modes.pop(session_id, None)
        self._seams.pop(session_id, None)
        self._brains.pop(session_id, None)
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
