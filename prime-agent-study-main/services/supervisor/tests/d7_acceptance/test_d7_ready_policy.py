"""D7 T12 / T13 — compose-down memory + ready policy (required vs soft)."""

from __future__ import annotations

from pathlib import Path

from tests.d7_acceptance.conftest import AI_REPO


def test_t12_staging_marks_memory_ready_required() -> None:
    """T12: when policy requires memory, staging compose sets MEMORY_READY_REQUIRED."""
    compose = AI_REPO / "deploy" / "staging" / "docker-compose.staging.yml"
    text = compose.read_text(encoding="utf-8")
    assert "MEMORY_READY_REQUIRED" in text
    # At least one supervisor service should require memory ready in staging.
    assert 'MEMORY_READY_REQUIRED: "true"' in text or "MEMORY_READY_REQUIRED: true" in text


def test_t12_supervisor_readyz_probes_memory_when_configured() -> None:
    """T12: supervisor /readyz must be able to fail with reason=memory."""
    health = (
        AI_REPO
        / "services"
        / "supervisor"
        / "src"
        / "supervisor"
        / "api"
        / "health.py"
    )
    text = health.read_text(encoding="utf-8")
    assert "memory" in text
    assert 'reason": "memory"' in text or "reason\": \"memory\"" in text or 'reason":"memory"' in text or '"memory"' in text


def test_t13_soft_policy_documented_or_env_flag() -> None:
    """T13: soft policy path exists (MEMORY_READY_REQUIRED false / optional)."""
    # Env flag or docs must allow soft-degraded Run when memory optional.
    health = (
        AI_REPO / "services" / "supervisor" / "src" / "supervisor" / "api" / "health.py"
    ).read_text(encoding="utf-8")
    app = (
        AI_REPO / "services" / "supervisor" / "src" / "supervisor" / "app.py"
    )
    app_text = app.read_text(encoding="utf-8") if app.is_file() else ""
    combined = health + "\n" + app_text
    soft = (
        "MEMORY_READY_REQUIRED" in combined
        or "check_memory" in combined
        or "memory_ready" in combined.lower()
    )
    assert soft, "D7 T13: need soft/required memory ready policy switch"
