"""Span closure reaper — Phase1 §3.2a triggers 3–5 (OTLP-bridge.md Step 2)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

from supervisor.obs.projector import JournalProjector

_log = logging.getLogger(__name__)


def _env_ms(name: str, default_ms: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default_ms
    try:
        return int(float(raw) * 1000) if "." in raw else int(raw)
    except ValueError:
        return default_ms


@dataclass
class ReaperConfig:
    """Defaults from Phase1 §3.2a — config, not literals."""

    run_idle_timeout_ms: int = 60 * 60 * 1000  # 60 min
    span_max_age_ms: int = 24 * 60 * 60 * 1000  # 24 h
    reaper_interval_s: float = 5 * 60  # 5 min
    tool_idle_timeout_ms: int = 30 * 60 * 1000  # 30 min (cascade when run idle)

    @classmethod
    def from_env(cls) -> ReaperConfig:
        return cls(
            run_idle_timeout_ms=_env_ms("YAL_RUN_IDLE_TIMEOUT_MS", 60 * 60 * 1000),
            span_max_age_ms=_env_ms("YAL_SPAN_MAX_AGE_MS", 24 * 60 * 60 * 1000),
            reaper_interval_s=float(os.environ.get("YAL_REAPER_INTERVAL_S", str(5 * 60))),
            tool_idle_timeout_ms=_env_ms("YAL_TOOL_IDLE_TIMEOUT_MS", 30 * 60 * 1000),
        )


@dataclass
class SpanReaper:
    """Periodic sweep over a projector. Reconstructible — no durable open-span store."""

    projector: JournalProjector
    config: ReaperConfig
    session_terminal: bool = False

    def tick(self, *, now_ms: int | None = None) -> str | None:
        """Apply idle / session_terminal / max_age. Returns closed_by if a run was closed."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        proj = self.projector
        if not proj.has_open_run:
            return None

        start = proj.run_start_ms or now
        last = proj.last_event_ms or start
        age = now - start
        idle = now - last

        if self.session_terminal:
            proj.force_close(end_ms=now, closed_by="session_terminal")
            return "session_terminal"

        if age > self.config.span_max_age_ms:
            _log.error(
                "span max_age fired run_id=%s age_ms=%s — closure path bug",
                proj.open_run_id,
                age,
            )
            proj.force_close(end_ms=now, closed_by="max_age")
            return "max_age"

        if idle > self.config.run_idle_timeout_ms:
            proj.force_close(end_ms=now, closed_by="idle_timeout")
            return "idle_timeout"

        return None


async def run_reaper_loop(
    reaper: SpanReaper,
    interval_s: float,
    stop: asyncio.Event,
) -> None:
    """Background loop: Event+timeout for prompt SIGTERM; never dies on tick errors."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except TimeoutError:
            pass
        try:
            closed_by = reaper.tick()
            if closed_by:
                _log.info("span_reaper closed run closed_by=%s", closed_by)
        except Exception:
            _log.exception("span_reaper tick failed")


def otlp_export_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
