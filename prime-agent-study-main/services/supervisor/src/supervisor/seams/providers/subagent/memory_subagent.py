"""Lab-echo SubagentProvider — in-process echo stub (NOT session / durable memory).

Production name: ``lab_echo``. Deprecated alias ``memory`` is registered only when
lab echo is explicitly allowed (local/tests). Never conflate with
``ContextMemoryProvider`` / Durable Memory (D7) — see ``contracts/memory.md``.
"""

from __future__ import annotations

from supervisor.seams.core.types import (
    ChildInfo,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
)
from supervisor.seams.interfaces.subagent_transport import (
    CAP_BACKGROUND,
    CAP_DEPTH_LIMIT,
    CAP_FORK,
    CAP_SPAWN,
)

# Canonical lab-only provider id (D12).
LAB_ECHO_PROVIDER = "lab_echo"
# Deprecated test alias — must not be the unmarked production default.
LEGACY_MEMORY_ALIAS = "memory"


class LabEchoSubagentProvider:
    """In-process echo stub for lab/tests — one-shot / fork / background.

    Intentionally has **no** ``start_continuable`` (fail-loud UnsupportedCapability).
    Not related to ``ContextMemoryProvider`` (compaction / recall seam).
    """

    name = LAB_ECHO_PROVIDER
    capabilities = frozenset({
        CAP_SPAWN,
        CAP_FORK,
        CAP_BACKGROUND,
        CAP_DEPTH_LIMIT,
    })

    def __init__(self) -> None:
        self._children: dict[str, ChildInfo] = {}
        self._seq = 0
        self.started = False

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _minted_or(self, request: SubagentRequest, prefix: str) -> str:
        if hasattr(request, 'child_id') and request.child_id:
            return request.child_id
        return self._next_id(prefix)

    def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        cid = self._minted_or(request, "child")
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        return SubagentRunResult(text=request.prompt, child_id=cid, status="ok")

    def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        cid = self._minted_or(request, "fork")
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        return SubagentRunResult(text=request.prompt, child_id=cid, status="ok")

    def start_background(self, request: SubagentRequest) -> JobHandle:
        self.started = True
        jid = self._next_id("job")
        cid = getattr(request, 'child_id', None) or self._next_id("child")
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        # Sync lab job: already complete when the handle returns.
        return JobHandle(job_id=jid)

    # Intentionally NO start_continuable — F-5 / D12 continuable fail-loud.

    def send_message(self, child_id: str, text: str) -> None:
        if child_id not in self._children:
            self._children[child_id] = ChildInfo(id=child_id, status="running")

    def interrupt(self, child_id: str) -> None:
        info = self._children.get(child_id)
        if info is not None:
            # ChildInfo is a dataclass, need to create new instance
            self._children[child_id] = ChildInfo(
                id=info.id,
                status="interrupted",
                description=info.description,
                pending=info.pending,
                live=info.live,
            )

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        _ = parent_id
        return list(self._children.values())


# Back-compat name used by older tests / imports.
MemorySubagentProvider = LabEchoSubagentProvider
