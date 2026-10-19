"""Nested-session SubagentProvider — real child path via model gateway (not echo).

Used as the production/staging default when ACP is not configured and lab echo
is disabled. Completes the child prompt through ``ModelGatewayClient`` so the
response is not a verbatim prompt echo.
"""

from __future__ import annotations

import uuid
from typing import Any

from supervisor.seams.interfaces.subagent_transport import (
    CAP_BACKGROUND,
    CAP_DEPTH_LIMIT,
    CAP_FORK,
    CAP_SPAWN,
)
from supervisor.seams.core.types import (
    ChatMessage,
    ChildInfo,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
)

NESTED_SESSION_PROVIDER = "nested_session"


class NestedSessionSubagentProvider:
    """In-process nested child that runs the prompt through the model gateway."""

    name = NESTED_SESSION_PROVIDER
    capabilities = frozenset({
        CAP_SPAWN,
        CAP_FORK,
        CAP_BACKGROUND,
        CAP_DEPTH_LIMIT,
    })

    def __init__(self, gateway: Any) -> None:
        if gateway is None:
            raise ValueError("gateway required for NestedSessionSubagentProvider")
        self._gateway = gateway
        self._children: dict[str, ChildInfo] = {}
        self.started = False

    def _child_id(self, request: SubagentRequest, prefix: str) -> str:
        if hasattr(request, 'child_id') and request.child_id:
            return request.child_id
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def _complete(self, request: SubagentRequest) -> str:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a nested child agent. Complete the task briefly."
                    + (f"\nTask label: {request.description}" if request.description else "")
                ),
            ),
            ChatMessage(role="user", content=request.prompt),
        ]
        completion = self._gateway.complete(messages, tools=[])
        msg = getattr(completion, "message", None)
        text = getattr(msg, "content", None) if msg is not None else None
        return str(text or "")

    def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        cid = self._child_id(request, "nested")
        text = self._complete(request)
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        return SubagentRunResult(
            text=text,
            child_id=cid,
            status="ok",
            meta={"transport": NESTED_SESSION_PROVIDER},
        )

    def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        cid = self._child_id(request, "nested-fork")
        text = self._complete(request)
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        return SubagentRunResult(
            text=text,
            child_id=cid,
            status="ok",
            meta={"transport": NESTED_SESSION_PROVIDER, "mode": "fork"},
        )

    def start_background(self, request: SubagentRequest) -> JobHandle:
        self.started = True
        cid = self._child_id(request, "nested")
        jid = f"job-{uuid.uuid4().hex[:10]}"
        text = self._complete(request)
        self._children[cid] = ChildInfo(
            id=cid, status="done", description=request.description
        )
        return JobHandle(job_id=jid)

    # No start_continuable — fail-loud until product implements it.

    def send_message(self, child_id: str, text: str) -> None:
        if child_id not in self._children:
            self._children[child_id] = ChildInfo(id=child_id, status="running")

    def interrupt(self, child_id: str) -> None:
        info = self._children.get(child_id)
        if info is not None:
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
