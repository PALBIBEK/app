"""Continuable in-process subagents (DeepSeek Activation subset).

Durable child sessions: start returns at inbox accept, parent steers with
`send_message`, current turn cancels via `interrupt_agent` (inbox kept),
children may `report` mid-turn, and the service injects a settlement notice
when a residency epoch goes idle.

After settle, the live Agent is parked (transcript kept in memory, status
`ready`). `send_message` cold-resumes by rebuilding the Agent from that
transcript. Disk persistence remains out of scope.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from ..agent.interrupt import Interrupt
from ..types import Message, StopReason, UserMessage
from . import SubagentError, assert_can_spawn

if TYPE_CHECKING:
    from ..agent.agent import Agent

# running = live turn; ready = parked (resumable, no live Agent).
ChildStatus = Literal["running", "ready"]


@dataclass
class ContinuableChild:
    """One durable child session owned by the parent registry."""

    id: str
    interrupt: Interrupt
    parent_depth: int = 0
    agent: Agent | None = None
    description: str = ""
    status: ChildStatus = "ready"
    messages: list[Message] = field(default_factory=list)
    last_output: str = ""
    last_stop_reason: StopReason | None = None
    last_error: str | None = None
    pending: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None
    announced: bool = False
    in_run: bool = False
    """True only while `agent.run()` is executing on the worker."""


def format_continuable_settlement(child: ContinuableChild) -> str:
    """Model-facing notice when a continuable residency epoch ends."""
    child_id = child.id
    if child.last_error and child.last_stop_reason is None:
        summary = f"Background subagent {child_id} failed before it finished."
        closing = child.last_error.strip()
    else:
        reason = child.last_stop_reason or StopReason.DONE
        summary = _settlement_summary(child_id, reason)
        closing = (child.last_output or "").strip()
    if not closing:
        closing = "It left no closing message."
    return f"{summary}\nIts closing message:\n{closing}"


def format_report_notice(child_id: str, output: str) -> str:
    """Model-facing frame for a child report relayed to the parent."""
    body = (output or "").strip()
    return f"Background subagent {child_id} reported:\n{body}"


def _settlement_summary(child_id: str, reason: StopReason) -> str:
    subject = f"Background subagent {child_id}"
    if reason is StopReason.DONE:
        return (
            f"{subject} finished and will do no further work "
            f"unless you send it more."
        )
    if reason is StopReason.INTERRUPTED:
        return f"{subject} was stopped before it finished."
    if reason is StopReason.MAX_TURNS:
        return f"{subject} ran out of room before it finished."
    if reason is StopReason.STOPPED:
        return f"{subject} declined the task."
    if reason is StopReason.ERROR:
        return f"{subject} failed before it finished."
    return (
        f"{subject} ended abnormally ({reason.value}) before it finished."
    )


class ContinuableRegistry:
    """Parent-owned registry for continuable child sessions."""

    def __init__(self, parent: Agent) -> None:
        self._parent = parent
        self._lock = threading.Lock()
        self._children: dict[str, ContinuableChild] = {}

    def get(self, child_id: str) -> ContinuableChild | None:
        with self._lock:
            return self._children.get(child_id)

    def start(
        self,
        prompt: str,
        *,
        description: str = "",
        parent_depth: int = 0,
    ) -> tuple[str, str]:
        """Accept the first prompt and return `(child_id, message_id)`.

        Resolves when the inbox accepts the message — not when the turn ends.
        """
        text = (prompt or "").strip()
        if not text:
            raise SubagentError(
                "subagent_continuable requires a non-empty `prompt`",
                "BAD_ARGS",
            )
        assert_can_spawn(parent_depth, self._parent.subagents.max_depth)

        child_id = uuid4().hex[:12]
        message_id = uuid4().hex[:12]
        interrupt = Interrupt()
        agent = self._parent.subagents._build_child(
            parent_depth=parent_depth,
            seed=None,
            interrupt=interrupt,
        )
        self._install_report(agent)
        child = ContinuableChild(
            id=child_id,
            interrupt=interrupt,
            parent_depth=parent_depth,
            agent=agent,
            description=(description or "").strip(),
            announced=True,
            status="running",
        )
        with self._lock:
            self._children[child_id] = child
        with child.lock:
            child.pending.append(text)
            self._ensure_worker_locked(child)
        return child_id, message_id

    def report_from(self, reporter: Agent, output: str) -> str:
        """Relay a child report to the live parent; return a message id.

        Does not settle the child or end its turn.
        """
        text = (output or "").strip()
        if not text:
            raise SubagentError(
                "report requires a non-empty `output`",
                "BAD_ARGS",
            )
        child = self._child_for_agent(reporter)
        if child is None:
            raise SubagentError(
                "agent is not a live continuable subagent and cannot report",
                "UNAUTHORIZED",
            )
        parent = self._parent
        if parent is None:
            raise SubagentError(
                "direct parent is not live; report was not delivered",
                "PARENT_UNAVAILABLE",
            )
        message_id = uuid4().hex[:12]
        notice = UserMessage(format_report_notice(child.id, text))
        parent.deliver_background_notice(notice)
        return message_id

    def send_message(self, child_id: str, message: str) -> str:
        """Queue `message` as the child's next turn; return a message id.

        Cold-resumes a parked (`ready`) child by rebuilding its Agent from the
        kept transcript before enqueueing.
        """
        text = (message or "").strip()
        if not text:
            raise SubagentError(
                "send_message requires a non-empty `message`",
                "BAD_ARGS",
            )
        child = self.get(child_id)
        if child is None:
            raise SubagentError(
                f"no continuable subagent named {child_id!r}",
                "NOT_FOUND",
            )
        message_id = uuid4().hex[:12]
        with child.lock:
            if (
                child.status == "running"
                and child.agent is not None
                and child.in_run
            ):
                # FIFO next turn inside the active run (do not steer).
                child.agent.follow_up(UserMessage(text))
            else:
                if child.agent is None:
                    self._materialize_locked(child)
                child.pending.append(text)
                child.status = "running"
                self._ensure_worker_locked(child)
        return message_id

    def interrupt(self, child_id: str) -> None:
        """Cancel the current turn; keep queued inbox / follow-up messages."""
        child = self.get(child_id)
        if child is None:
            return
        with child.lock:
            if child.status == "running" and child.agent is not None:
                child.interrupt.set()

    def interrupt_all(self) -> bool:
        """Interrupt every running continuable. Returns True if any signaled."""
        with self._lock:
            children = list(self._children.values())
        signaled = False
        for child in children:
            with child.lock:
                if child.status == "running" and child.agent is not None:
                    child.interrupt.set()
                    signaled = True
        return signaled

    def list_children(self) -> list[dict[str, Any]]:
        """Snapshot for list_agents (continuable only)."""
        with self._lock:
            children = list(self._children.values())
        out: list[dict[str, Any]] = []
        for child in children:
            with child.lock:
                out.append({
                    "id": child.id,
                    "status": child.status,
                    "description": child.description,
                    "pending": len(child.pending),
                    "live": child.agent is not None,
                })
        return out

    def _child_for_agent(self, reporter: Agent) -> ContinuableChild | None:
        with self._lock:
            children = list(self._children.values())
        for child in children:
            if child.agent is reporter:
                return child
        return None

    def _install_report(self, agent: Agent) -> None:
        """Add the child-only `report` tool and bind a sink for ToolContext."""
        from ..tools.report import report_tool

        agent.tools.add(report_tool())
        # System prompt tool listing is a snapshot; refresh so the model sees report.
        agent.config.tools_for_prompt = [
            agent.tools.get(name) for name in agent.tools.names()
        ]
        agent.report_sink = lambda output, _agent=agent: self.report_from(
            _agent, output
        )

    def _materialize_locked(self, child: ContinuableChild) -> None:
        """Rebuild a live Agent from the parked transcript. Holds `child.lock`."""
        if child.agent is not None:
            return
        seed = copy.deepcopy(child.messages) if child.messages else None
        agent = self._parent.subagents._build_child(
            parent_depth=child.parent_depth,
            seed=seed,
            interrupt=child.interrupt,
        )
        self._install_report(agent)
        child.agent = agent

    def _cold_park_locked(self, child: ContinuableChild) -> None:
        """Drop the live Agent; keep a deep-copied transcript. Holds `child.lock`."""
        agent = child.agent
        if agent is not None:
            child.messages = copy.deepcopy(list(agent.state.messages))
            agent.report_sink = None
            agent.ask_user = None
            from ..sandbox.active_runs import BashRunRegistry

            agent.bash_runs = BashRunRegistry()
            child.agent = None
        child.status = "ready"
        child.interrupt.clear()

    def _ensure_worker_locked(self, child: ContinuableChild) -> None:
        """Start a worker if none is alive. Caller holds `child.lock`."""
        thread = child.thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=self._worker,
            args=(child,),
            name=f"githi-continuable-{child.id}",
            daemon=True,
        )
        child.thread = thread
        thread.start()

    def _worker(self, child: ContinuableChild) -> None:
        while True:
            with child.lock:
                if not child.pending:
                    should_settle = child.announced
                    prompt = None
                else:
                    should_settle = False
                    if child.agent is None:
                        self._materialize_locked(child)
                    prompt = child.pending.pop(0)
                    child.status = "running"
                    child.interrupt.clear()
                    child.last_error = None

            if should_settle:
                self._deliver_settlement(child)
                with child.lock:
                    # Late send_message during settlement lands on pending
                    # (in_run is false). Also re-absorb any stray follow_ups.
                    self._absorb_late_follow_ups(child)
                    if child.pending:
                        child.status = "running"
                        continue
                    self._cold_park_locked(child)
                    if child.thread is threading.current_thread():
                        child.thread = None
                    return

            assert prompt is not None
            assert child.agent is not None
            try:
                with child.lock:
                    child.in_run = True
                try:
                    result = child.agent.run(prompt)
                finally:
                    with child.lock:
                        child.in_run = False
                with child.lock:
                    child.last_output = result.output or ""
                    child.last_stop_reason = result.stop_reason
                    self._absorb_late_follow_ups(child)
            except Exception as exc:  # noqa: BLE001 - always settle
                with child.lock:
                    child.in_run = False
                    child.last_error = str(exc)
                    child.last_stop_reason = StopReason.ERROR
                    child.last_output = ""
                    self._absorb_late_follow_ups(child)

    def _absorb_late_follow_ups(self, child: ContinuableChild) -> None:
        """Move follow-ups that arrived after run() returned into pending.

        Caller holds `child.lock`.
        """
        if child.agent is None:
            return
        for message in child.agent._follow_up.drain():
            text = getattr(message, "content", "") or ""
            text = str(text).strip()
            if text:
                child.pending.append(text)

    def _deliver_settlement(self, child: ContinuableChild) -> None:
        notice = UserMessage(format_continuable_settlement(child))
        self._parent.deliver_background_notice(notice)
