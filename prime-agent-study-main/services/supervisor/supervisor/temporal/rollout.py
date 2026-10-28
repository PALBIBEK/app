"""Safe deploys: versioned workers, a controlled rollout, and a replay gate.

The Temporal pattern for changing code under runs in flight:

- **Worker versioning.** Each deploy's workers register as one *version* (a build id) of a
  *worker deployment* (``WORKER_DEPLOYMENT_NAME`` / ``WORKER_BUILD_ID``). New workflows go to
  the deployment's *current* version, and every workflow is ``AUTO_UPGRADE``: a session lives
  for hours or days, so it moves to the new build at its next workflow task, and the old
  workers can stop as soon as the new build is current (their in-flight steps finish or are
  adopted). Changes that would alter recorded history are guarded with ``workflow.patched``,
  and the replay gate checks them. A ramp sends a share of *new* sessions to the new build
  first, so a bad build meets few users before it is promoted.
- **Rollout.** ``python -m supervisor rollout status | ramp BUILD PCT | promote BUILD``. A
  version's first worker promotes itself when the deployment has no current version yet.
  With ``WORKER_ROLLOUT=auto`` (the default, for single-host deploys) each new build promotes
  itself once polling; with ``manual`` an operator or the CD pipeline ramps and promotes.
- **Replay gate.** ``python -m supervisor rollout check`` replays the histories of running
  (and recent) workflows against the code about to ship, and fails on any history it cannot
  reproduce. CI does the same with histories committed from earlier releases
  (``tests/fixtures/histories``), so an incompatible change fails before it merges.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from temporalio.api.enums.v1 import EventType, VersionDrainageStatus, WorkerDeploymentVersionStatus
from temporalio.client import Client, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import DataConverter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer

logger = logging.getLogger(__name__)

IDENTITY: Final = "supervisor rollout"
WORKFLOW_TYPES: Final = ("AgentSession", "SubagentJob")

_STATUS: Final = {
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_INACTIVE: "inactive",
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_CURRENT: "current",
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_RAMPING: "ramping",
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_DRAINING: "draining",
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_DRAINED: "drained",
    WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_CREATED: "created",
}
_DRAINAGE: Final = {
    VersionDrainageStatus.VERSION_DRAINAGE_STATUS_DRAINING: "draining",
    VersionDrainageStatus.VERSION_DRAINAGE_STATUS_DRAINED: "drained",
}


def _build(version: str, structured: Any) -> str | None:
    """A version's build id: the structured field where the server fills it, else the
    ``<deployment>.<build>`` string older servers use."""
    build = str(getattr(structured, "build_id", "") or "") if structured is not None else ""
    if build:
        return build
    if not version or version == "__unversioned__":
        return None
    return version.split(".", 1)[1] if "." in version else version


@dataclass(frozen=True)
class VersionState:
    build_id: str
    status: str  # current | ramping | draining | drained | inactive | created
    drainage: str = ""  # draining | drained (pinned runs still on it, or none)

    @property
    def safe_to_retire(self) -> bool:
        """No workflow needs this build any more: its workers can be stopped."""
        return self.status in ("drained", "inactive") or (
            self.status not in ("current", "ramping") and self.drainage == "drained"
        )


@dataclass(frozen=True)
class RolloutStatus:
    deployment: str
    current_build: str | None
    ramping_build: str | None = None
    ramping_percentage: float = 0.0
    versions: list[VersionState] = field(default_factory=list)

    def version(self, build_id: str) -> VersionState | None:
        return next((v for v in self.versions if v.build_id == build_id), None)


async def status(client: Client, deployment: str) -> RolloutStatus | None:
    """The deployment's routing and versions; None until its first worker has polled."""
    from temporalio.api.workflowservice.v1 import DescribeWorkerDeploymentRequest

    try:
        resp = await client.workflow_service.describe_worker_deployment(
            DescribeWorkerDeploymentRequest(namespace=client.namespace, deployment_name=deployment)
        )
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise
    info = resp.worker_deployment_info
    routing = info.routing_config
    versions = [
        VersionState(
            build_id=_build(s.version, s.deployment_version if s.HasField("deployment_version") else None) or "",
            status=_STATUS.get(s.status, "unknown"),
            drainage=_DRAINAGE.get(s.drainage_status, ""),
        )
        for s in info.version_summaries
    ]
    current = _build(
        routing.current_version,
        routing.current_deployment_version if routing.HasField("current_deployment_version") else None,
    )
    ramping = _build(
        routing.ramping_version,
        routing.ramping_deployment_version if routing.HasField("ramping_deployment_version") else None,
    )
    # Older servers leave the per-version status unset: derive it from the routing.
    versions = [
        v if v.status != "unknown" else VersionState(
            v.build_id,
            "current" if v.build_id == current else "ramping" if v.build_id == ramping else "inactive",
            v.drainage,
        )
        for v in versions
    ]
    return RolloutStatus(
        deployment=deployment,
        current_build=current,
        ramping_build=ramping,
        ramping_percentage=float(routing.ramping_version_percentage) if ramping else 0.0,
        versions=sorted(versions, key=lambda v: v.build_id),
    )


async def promote(client: Client, deployment: str, build_id: str) -> None:
    """Make ``build_id`` current: new workflows (and auto-upgrading sessions) run on it.

    Every worker also polls a queue of its own (the session it holds lives there), which a new
    build never has; the server would refuse the change for those missing queues. They are
    meant to go with their worker (its sessions are adopted elsewhere), so the change says so
    (``ignore_missing_task_queues``).
    """
    from temporalio.api.workflowservice.v1 import SetWorkerDeploymentCurrentVersionRequest

    await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=client.namespace, deployment_name=deployment, build_id=build_id, identity=IDENTITY,
            ignore_missing_task_queues=True,
        )
    )


async def ramp(client: Client, deployment: str, build_id: str, percentage: float) -> None:
    """Send ``percentage`` of new workflows to ``build_id`` (0 stops the ramp)."""
    from temporalio.api.workflowservice.v1 import SetWorkerDeploymentRampingVersionRequest

    if not 0 <= percentage <= 100:
        raise ValueError("percentage must be between 0 and 100")
    await client.workflow_service.set_worker_deployment_ramping_version(
        SetWorkerDeploymentRampingVersionRequest(
            namespace=client.namespace, deployment_name=deployment,
            build_id=build_id if percentage > 0 else "", percentage=percentage, identity=IDENTITY,
            ignore_missing_task_queues=True,
        )
    )


async def bootstrap(
    client: Client, deployment: str, build_id: str, *, mode: str = "auto", timeout_s: float = 120.0
) -> str:
    """Called by a versioned worker once it polls. Promotes its build when the deployment has no
    current version (a first deploy must not leave new work with nowhere to go), or always with
    ``mode="auto"``. Returns what it did: ``promoted``, ``already_current`` or ``left``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        try:
            found = await status(client, deployment)
            if found is not None and found.version(build_id) is not None:
                break
        except RPCError as exc:
            logger.info("waiting for the worker deployment to register: %s", exc)
        if loop.time() > deadline:
            raise TimeoutError(f"version {deployment}.{build_id} did not register in {timeout_s:g}s")
        await asyncio.sleep(1.0)
    if found.current_build == build_id:
        return "already_current"
    if mode == "auto" or found.current_build is None:
        await promote(client, deployment, build_id)
        logger.info("promoted %s.%s to current (was %s)", deployment, build_id, found.current_build)
        return "promoted"
    logger.info(
        "version %s.%s is polling; current stays %s (WORKER_ROLLOUT=manual: ramp or promote it)",
        deployment, build_id, found.current_build,
    )
    return "left"


# --- the replay gate -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayFailure:
    workflow_id: str
    error: str


@dataclass(frozen=True)
class ReplayReport:
    checked: int
    failures: list[ReplayFailure]

    @property
    def ok(self) -> bool:
        return not self.failures


def _replayer(converter: DataConverter | None = None) -> Replayer:
    from supervisor.temporal.job_workflow import SubagentJobWorkflow
    from supervisor.temporal.workflow import AgentSessionWorkflow

    return Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=converter or pydantic_data_converter,
    )


def _replayable(history: WorkflowHistory) -> bool:
    # A run no worker picked up yet holds no workflow code to check (and would hang the replayer).
    return any(e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED for e in history.events)


async def replay(
    histories: AsyncIterator[WorkflowHistory], converter: DataConverter | None = None
) -> ReplayReport:
    """Replay ``histories`` against the workflow code in this process."""
    checked, failures = 0, []

    async def usable() -> AsyncIterator[WorkflowHistory]:
        async for h in histories:
            if _replayable(h):
                yield h

    # Sealed histories (a codec key) open with the converter that sealed them.
    async with _replayer(converter).workflow_replay_iterator(usable()) as results:
        async for result in results:
            checked += 1
            if result.replay_failure is not None:
                failures.append(ReplayFailure(result.history.workflow_id, str(result.replay_failure)))
    return ReplayReport(checked, failures)


async def live_histories(client: Client, *, limit: int = 50) -> AsyncIterator[WorkflowHistory]:
    """Running workflows first (they are the ones a deploy must not break), then recent ones."""
    types = " or ".join(f"WorkflowType = '{t}'" for t in WORKFLOW_TYPES)
    seen = 0
    for query in (f"({types}) and ExecutionStatus = 'Running'", f"({types}) and ExecutionStatus != 'Running'"):
        async for run in client.list_workflows(query):
            if seen >= limit:
                return
            yield await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
            seen += 1


async def check(client: Client, *, limit: int = 50) -> ReplayReport:
    """The pre-deploy gate: can this code replay what is running now?"""
    return await replay(live_histories(client, limit=limit), client.data_converter)


async def file_histories(directory: Path) -> AsyncIterator[WorkflowHistory]:
    """Saved histories: ``export``'s files (``workflow_id`` and ``history``), or a bare history
    as Temporal's UI and CLI download it (named after its file)."""
    for path in sorted(directory.rglob("*.json")):
        doc = json.loads(path.read_text())
        if "history" in doc:
            yield WorkflowHistory.from_json(doc["workflow_id"], doc["history"])
        else:
            yield WorkflowHistory.from_json(path.stem, doc)


async def export(client: Client, directory: Path, *, limit: int = 50) -> int:
    """Save histories (one JSON file each) for the CI replay gate."""
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    async for history in live_histories(client, limit=limit):
        if not _replayable(history):
            continue
        name = history.workflow_id.replace("/", "_")
        (directory / f"{name}.json").write_text(
            json.dumps({"workflow_id": history.workflow_id, "history": json.loads(history.to_json())})
        )
        count += 1
    return count
