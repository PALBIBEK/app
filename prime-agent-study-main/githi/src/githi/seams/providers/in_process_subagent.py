"""In-process SubagentProvider wrapping SubagentService + jobs + continuable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...subagent import SubagentRunResult, SubagentService
from ...subagent.continuable import ContinuableRegistry
from ...subagent.jobs import SubagentJobRegistry
from ..subagent_transport import (
    CAP_BACKGROUND,
    CAP_CONTINUABLE,
    CAP_DEPTH_LIMIT,
    CAP_FORK,
    CAP_SPAWN,
    ChildHandle,
    ChildInfo,
    JobHandle,
    SubagentRequest,
)

if TYPE_CHECKING:
    from ...agent.agent import Agent


class InProcessSubagentProvider:
    """First provider (`in_process`) over today's three parent-bound registries."""

    name = "in_process"
    capabilities = frozenset({
        CAP_SPAWN,
        CAP_FORK,
        CAP_BACKGROUND,
        CAP_CONTINUABLE,
        CAP_DEPTH_LIMIT,
    })

    def __init__(self) -> None:
        self._parent: Agent | None = None
        self.service: SubagentService | None = None
        self.jobs: SubagentJobRegistry | None = None
        self.continuable: ContinuableRegistry | None = None

    def bind_parent(self, parent: Agent) -> None:
        """Construct registries against `parent` (called from factory / transport)."""
        self._parent = parent
        self.service = SubagentService(parent)
        self.jobs = SubagentJobRegistry(parent)
        self.continuable = ContinuableRegistry(parent)

    def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
        assert self.service is not None
        return self.service.start_one_shot(
            request.prompt,
            description=request.description,
            parent_depth=request.parent_depth,
            interrupt=request.interrupt,
            run_id=request.run_id,
        )

    def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
        assert self.service is not None
        return self.service.start_fork(
            request.prompt,
            description=request.description,
            parent_depth=request.parent_depth,
            interrupt=request.interrupt,
            run_id=request.run_id,
            seed=request.seed,
        )

    def start_background(self, request: SubagentRequest) -> JobHandle:
        assert self.jobs is not None
        if request.mode == "fork":
            job_id = self.jobs.start_fork(
                request.prompt,
                description=request.description,
                parent_depth=request.parent_depth,
            )
        else:
            job_id = self.jobs.start_spawn(
                request.prompt,
                description=request.description,
                parent_depth=request.parent_depth,
            )
        return JobHandle(job_id=job_id)

    def start_continuable(self, request: SubagentRequest) -> ChildHandle:
        assert self.continuable is not None
        child_id, message_id = self.continuable.start(
            request.prompt,
            description=request.description,
            parent_depth=request.parent_depth,
        )
        return ChildHandle(child_id=child_id, message_id=message_id)

    def send_message(self, child_id: str, text: str) -> None:
        assert self.continuable is not None
        self.continuable.send_message(child_id, text)

    def interrupt(self, child_id: str) -> None:
        assert self.continuable is not None
        self.continuable.interrupt(child_id)

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        assert self.continuable is not None
        raw = self.continuable.list_children()
        return [
            ChildInfo(
                id=row["id"],
                status=row["status"],
                description=row.get("description", ""),
                pending=int(row.get("pending", 0)),
                live=bool(row.get("live", False)),
            )
            for row in raw
        ]
