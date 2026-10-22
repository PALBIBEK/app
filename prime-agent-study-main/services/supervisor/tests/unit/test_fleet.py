"""Fleet health rules, its Prometheus text, the worker's load counter and health answers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from supervisor.__main__ import main as role_main
from supervisor.config import TemporalSettings
from supervisor.temporal.fleet import Load, QueueStats, WorkerBeat, assess, prometheus
from supervisor.temporal.worker import Health, health_app

SERVED = QueueStats(workflow_pollers=["w-1"], activity_pollers=["w-1", "w-2"])


def _beat(worker: str, max_runs: int, busy: int) -> WorkerBeat:
    return WorkerBeat(worker, "h", "q", "home", max_runs, busy, 0, 0)


def test_a_served_idle_queue_is_ok() -> None:
    s = assess("q", SERVED, [_beat("w-1", 10, 2), _beat("w-2", 10, 0)])
    assert (s.status, s.polling, s.slots_max, s.slots_busy, s.alerts) == ("ok", 2, 20, 2, [])


def test_nobody_polling_is_down_even_with_a_leftover_heartbeat() -> None:
    s = assess("q", QueueStats(workflow_pollers=["w-1"]), [_beat("w-1", 10, 0)])
    assert s.status == "down" and [a.code for a in s.alerts] == ["no_workers"]


def test_old_waiting_work_and_full_slots_degrade() -> None:
    q = QueueStats(workflow_pollers=["w-1"], activity_pollers=["w-1"], backlog=4, backlog_age_s=45)
    s = assess("q", q, [_beat("w-1", 2, 2)], backlog_alert_s=30)
    assert s.status == "degraded" and [a.code for a in s.alerts] == ["backlog", "at_capacity"]
    assert "45s" in s.alerts[0].message and "2/2" in s.alerts[1].message
    young = QueueStats(workflow_pollers=["w"], activity_pollers=["w"], backlog=4, backlog_age_s=2)
    assert assess("q", young, [_beat("w", 2, 0)]).status == "ok"  # a moment's queue is normal


def test_a_poller_without_a_fresh_heartbeat_has_stopped() -> None:
    # The server lists a stopped worker's poller for a while; its heartbeat is gone at once.
    assert assess("q", SERVED, []).status == "down"
    assert assess("q", SERVED, [], heartbeats=False).status == "ok"  # where workers do not beat


def test_prometheus_text() -> None:
    text = prometheus(assess('a"q', QueueStats(backlog=3, backlog_age_s=12.5), []))
    assert 'yal_workers_serving{task_queue="a\\"q"} 0' in text
    assert 'yal_task_queue_backlog{task_queue="a\\"q"} 3' in text
    assert "yal_task_queue_backlog_age_seconds" in text and text.endswith("\n")


@pytest.mark.asyncio
async def test_load_counts_running_activities() -> None:
    load = Load()
    gate = asyncio.Event()

    class _Next:
        async def execute_activity(self, input: Any) -> str:
            await gate.wait()
            return "done"

    inbound = load.interceptor().intercept_activity(_Next())  # type: ignore[arg-type]
    running = [asyncio.create_task(inbound.execute_activity(None)) for _ in range(2)]  # type: ignore[arg-type]
    await asyncio.sleep(0)
    assert load.busy == 2
    gate.set()
    assert await asyncio.gather(*running) == ["done", "done"]
    assert load.busy == 0


def test_worker_health_answers() -> None:
    health = Health()
    api = TestClient(health_app(health))
    assert api.get("/healthz").status_code == 200
    assert api.get("/readyz").json() == {"status": "unhealthy", "reason": "starting"}
    health.serving = True
    health.on_beat(True)
    assert api.get("/readyz").status_code == 200
    health.last_beat_ok_at -= 3600  # heartbeats stopped landing
    assert api.get("/healthz").status_code == 503
    health.on_beat(True)
    health.stopping = True
    assert api.get("/readyz").json()["reason"] == "draining" and api.get("/healthz").status_code == 200


def test_worker_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TemporalSettings.from_env().max_concurrent_runs == 50
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_RUNS", "8")
    monkeypatch.setenv("WORKER_HEALTH_PORT", "8081")
    monkeypatch.setenv("WORKER_BACKLOG_ALERT_S", "12")
    t = TemporalSettings.from_env()
    assert (t.max_concurrent_runs, t.worker_health_port, t.backlog_alert_s) == (8, 8081, 12.0)


def test_one_image_two_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr("supervisor.main.main", lambda: called.append("api"))
    monkeypatch.setattr("supervisor.temporal.worker.main", lambda: called.append("worker"))
    role_main([])
    role_main(["api"])
    role_main(["worker"])
    assert called == ["api", "api", "worker"]
    with pytest.raises(SystemExit, match="unknown role"):
        role_main(["gateway"])
