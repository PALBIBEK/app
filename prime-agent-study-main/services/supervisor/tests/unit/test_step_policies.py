"""One table of step policies, used by the workflows and printed in the README."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from temporalio import workflow as wf
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion

from supervisor.config import TemporalSettings
from supervisor.temporal.contracts import Activity, Timing
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.policies import POLICIES, RETRIES, Limit, Retry, policy, policy_table
from supervisor.temporal.rollout import VersionState, _build
from supervisor.temporal.worker import deployment_config
from supervisor.temporal.workflow import AgentSessionWorkflow

SRC = Path(__file__).resolve().parents[2] / "supervisor" / "temporal"
README = Path(__file__).resolve().parents[5] / "README.md"


def test_every_activity_has_a_policy() -> None:
    assert set(POLICIES) == set(Activity)


def test_the_workflows_take_limits_and_retries_from_the_table() -> None:
    for name in ("workflow.py", "job_workflow.py"):
        code = (SRC / name).read_text()
        assert "RetryPolicy(" not in code, f"{name} builds its own retry policy"
        assert not re.search(r"start_to_close_timeout=(timedelta|_)", code), f"{name} sets its own time limit"


def test_limits_follow_the_deployments_timing() -> None:
    t = Timing(heartbeat_timeout_s=7, turn_timeout_s=600)
    assert policy(Activity.TOOL_CALL).start_to_close(t).total_seconds() == 600
    assert policy(Activity.TOOL_CALL).heartbeat_timeout(t).total_seconds() == 7  # type: ignore[union-attr]
    assert policy(Activity.CREATE).heartbeat_timeout(t) is None
    assert policy(Activity.MODEL_STEP).limit is Limit.MODEL_CALL
    assert policy(Activity.END_JOB).retry_policy is RETRIES[Retry.UNTIL_DONE]
    assert RETRIES[Retry.ONCE].maximum_attempts == 1


def test_long_steps_heartbeat_so_a_dead_worker_is_seen_quickly() -> None:
    for activity, p in POLICIES.items():
        if p.limit is not Limit.SHORT:
            assert p.heartbeat, f"{activity} may run long without a heartbeat"


def test_the_readme_shows_the_current_table() -> None:
    text = README.read_text()
    start, end = "<!-- step-policies:start -->", "<!-- step-policies:end -->"
    assert start in text and end in text, "README lacks the step policy table"
    shown = text.split(start, 1)[1].split(end, 1)[0].strip()
    assert shown == policy_table(), "README table is stale: python -m supervisor rollout policies"


def test_versioned_workers_auto_upgrade_every_workflow() -> None:
    config = deployment_config(WorkerDeploymentVersion("yal", "b2"))
    assert config.use_worker_versioning and config.version.build_id == "b2"
    assert config.default_versioning_behavior is VersioningBehavior.AUTO_UPGRADE
    # Declared on the worker, not the class: Temporal refuses a class-level behaviour where
    # workers run unversioned (every deployment that does not set WORKER_DEPLOYMENT_NAME).
    for cls in (AgentSessionWorkflow, SubagentJobWorkflow):
        assert wf._Definition.must_from_class(cls).versioning_behavior is VersioningBehavior.UNSPECIFIED


def test_versioning_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not TemporalSettings.from_env().versioned
    monkeypatch.setenv("WORKER_DEPLOYMENT_NAME", "yal")
    monkeypatch.setenv("WORKER_BUILD_ID", "abc123")
    monkeypatch.setenv("WORKER_ROLLOUT", "manual")
    t = TemporalSettings.from_env()
    assert (t.versioned, t.deployment_name, t.build_id, t.rollout) == (True, "yal", "abc123", "manual")
    monkeypatch.setenv("WORKER_ROLLOUT", "sometimes")
    with pytest.raises(ValueError, match="WORKER_ROLLOUT"):
        TemporalSettings.from_env()


def test_version_names_and_retiring() -> None:
    assert _build("yal.b2", None) == "b2" and _build("__unversioned__", None) is None and _build("", None) is None
    assert VersionState("b1", "drained").safe_to_retire
    assert VersionState("b1", "draining", "drained").safe_to_retire
    assert not VersionState("b1", "draining", "draining").safe_to_retire
    assert not VersionState("b2", "current").safe_to_retire
