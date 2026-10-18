"""The agent: prompt in, answer out.

`Agent` owns what persists *across* runs -- the transcript, the steering and
follow-up queues, listeners, the session file. The turns themselves belong to
`run_loop`, which this delegates to.

That split was not always clean. For a while both this class and `run_loop`
carried their own copy of the turn loop, and they drifted: `run_loop` had the
truncated-tool-call and provider-error handling, this one had compaction and
the turn budget, and the loop that actually ran in production was whichever
the caller happened to reach. Both test suites passed the whole time, because
neither knew about the other. Everything the inline loop did is now expressed
as `LoopConfig` fields and hooks, so there is one implementation to fix.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..harness.branch_summary import format_branch_summary, summarize_branch
from ..harness.compaction import DEFAULT_LIMIT_TOKENS, SUMMARY_MARKER, compact
from ..harness.session import Session
from ..harness.session_view import format_tree, leaves, resolve_leaf
from ..llm.usage import UsageTracker
from ..sandbox.policy import SandboxPolicy
from ..sandbox.active_runs import BashRunRegistry
from ..seams.prompt_assembler import PromptAssembler, PromptBindings
from ..seams.subagent_transport import SubagentTransport
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from ..types import (
    AgentContext,
    Message,
    QueueMode,
    StopReason,
    SystemMessage,
    ThinkingLevel,
    UserMessage,
)
from .events import Event, EventType
from .hooks import Hooks
from .loop import LoopConfig, run_loop
from .state import AgentState


class PendingMessageQueue:
    """Messages a human queued while the agent was working (pi's
    `PendingMessageQueue`).

    `QueueMode.ONE_AT_A_TIME` exists because handing the model five queued
    corrections simultaneously reads as five contradictory instructions
    rather than a sequence of refinements — the later ones were written
    knowing the earlier ones had already landed.
    """

    def __init__(self, mode: QueueMode = QueueMode.ALL) -> None:
        self.mode = mode
        self._messages: list[Message] = []
        self._lock = threading.Lock()

    def enqueue(self, message: Message) -> None:
        with self._lock:
            self._messages.append(message)

    def has_items(self) -> bool:
        with self._lock:
            return bool(self._messages)

    def drain(self) -> list[Message]:
        """Take what this mode allows. Always returns a fresh list, so a
        caller mutating the result cannot corrupt the queue."""
        with self._lock:
            if self.mode is QueueMode.ALL:
                drained, self._messages = list(self._messages), []
                return drained
            if not self._messages:
                return []
            first, self._messages = self._messages[0], self._messages[1:]
            return [first]

    def clear(self) -> None:
        with self._lock:
            self._messages = []


@dataclass
class AgentConfig:
    max_turns: int = 30
    system_prompt: str = ""
    skill_listing: str = ""
    context_limit_tokens: int = DEFAULT_LIMIT_TOKENS
    session_path: Path | None = None
    cwd: str = "."
    tools_for_prompt: list = field(default_factory=list)
    """Tools listed by name in the prompt's inventory, matching pi's
    `toolSnippets`. Separate from the registry because a caller may expose a
    tool without advertising it in the prompt."""
    warn_turns_remaining: int = 3
    """Start telling the model how many turns are left once it gets this
    close to the limit.

    Observed live: on a bisection task the agent found the correct answer,
    then spent its last turns fetching one field at a time -- sha, then PR
    number, then the diff -- and hit the wall without ever stating a
    conclusion it already had. Nothing in its context distinguished turn 3
    from turn 39. Raising the limit only moves the wall; telling it the
    budget is what changes the behaviour."""

    interrupt: Any = None
    """The ESC flag, shared with a `KeyReader`. Anything with `is_set()`."""

    memory: Any = None
    """A `MemoryService`, or anything with `recall_block(query)`.

    Recall runs once per user prompt and is injected through the `next_turn`
    queue. Optional: absent memory means no injection, never an error."""

    summarizer: Any = None
    """Turns the span compaction drops into a structured checkpoint.

    Optional on purpose. Without it compaction still works, it just leaves a
    notice instead of the findings -- so a provider outage degrades what
    survives rather than ending the run. `build_summarizer` in
    `harness.compaction` wires one to an LLM client."""

    steering_mode: QueueMode = QueueMode.ALL
    follow_up_mode: QueueMode = QueueMode.ALL

    force_answer_on_final_turn: bool = True
    """On the last turn, offer no tools at all.

    A warning is advice the model may ignore; withholding tools is
    structural. With an empty tool list it cannot call anything, so the only
    move left is to answer -- which converts a silent MAX_TURNS truncation
    into a real, if rushed, result."""


@dataclass
class RunResult:
    output: str
    stop_reason: StopReason
    turns: int
    tool_calls: int
    usage: UsageTracker
    state: AgentState = field(repr=False, default=None)  # type: ignore[assignment]


class Agent:
    def __init__(
        self,
        llm: Any,
        tools: ToolRegistry,
        policy: SandboxPolicy,
        config: AgentConfig | None = None,
        hooks: Hooks | None = None,
        *,
        subagent_transport: SubagentTransport | None = None,
        prompt_assembler: PromptAssembler | None = None,
        gateway: Any | None = None,
        seams: Any | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.policy = policy
        self.config = config or AgentConfig()
        self.hooks = hooks or Hooks()
        self.state = AgentState()
        self._session = Session(self.config.session_path) if self.config.session_path else None
        if self._session:
            self.state.messages[:] = self._session.load()
        self._steering = PendingMessageQueue(self.config.steering_mode)
        self._follow_up = PendingMessageQueue(self.config.follow_up_mode)
        self._next_turn = PendingMessageQueue(QueueMode.ALL)
        self._model: str | None = None
        self._thinking: ThinkingLevel | None = None
        self.bash_runs = BashRunRegistry()
        self.ask_user = None
        """Callable set by the REPL: questions -> AskAnswer.

        None outside an interactive UI so the tool fails with a clear
        message instead of hanging (DeepSeek disables ask-user on headless
        children for the same reason)."""
        self.delegation_depth = 0
        """How many spawn hops above this agent (0 = top-level)."""
        self.delegated = False
        """True for children owned by another live agent (blocks ask_user)."""
        # Façades filled by seams.bind_parent (never constructed here — P-3 / A-4).
        self.subagents = None
        """One-shot foreground spawn service (DeepSeek ctx.subagents subset)."""
        self.subagent_jobs = None
        """Background one-shot jobs + settlement notices."""
        self.subagent_continuable = None
        """Durable continuable children + send_message / settlement."""
        self.subagent_transport: SubagentTransport | None = subagent_transport
        self.prompt_assembler: PromptAssembler | None = prompt_assembler
        self.gateway = gateway
        self.report_sink = None
        """Bound only on continuable children; relays `report` to the parent."""
        self._run_active = False
        self._delivery_lock = threading.Lock()
        self._wire_seams(seams)

    def _wire_seams(self, seams: Any | None) -> None:
        """Bind injected or default seams without constructing providers inline."""
        from ..seams.factory import build_default_seams
        from ..seams.providers.default_prompt import DefaultPromptAssembler

        explicit_assembler = self.prompt_assembler
        explicit_gateway = self.gateway
        if seams is None and self.subagent_transport is None:
            seams = build_default_seams(llm=self.llm, tools_registry=self.tools)
        if seams is not None:
            seams.bind_parent(self)
            self.subagent_transport = seams.subagents
            self.prompt_assembler = explicit_assembler or seams.prompt_assembler
            self.gateway = (
                explicit_gateway
                if explicit_gateway is not None
                else (seams.gateway if seams.gateway is not None else self.llm)
            )
            return
        assert self.subagent_transport is not None
        bind = getattr(self.subagent_transport, "bind_parent", None)
        if callable(bind):
            bind(self)
        self.prompt_assembler = explicit_assembler or DefaultPromptAssembler()
        self.gateway = explicit_gateway if explicit_gateway is not None else self.llm

    def on(self, listener) -> None:
        self.state.on(listener)

    def subscribe(self, listener):
        """Register a listener and return a function that removes it.

        pi returns an unsubscribe closure rather than exposing a remove()
        that needs the same object back — a listener you cannot detach leaks
        for the lifetime of the agent, and callers rarely keep the reference.
        """
        self.state.on(listener)
        return lambda: self.state.off(listener)

    # ---------------- queues ----------------

    def steer(self, message: Message) -> None:
        """Queue a message for *during* the run — delivered before the next
        assistant turn, so it redirects work already in flight."""
        self._steering.enqueue(message)

    def follow_up(self, message: Message) -> None:
        """Queue a message for *after* the agent would have stopped, which
        restarts it rather than requiring a fresh run."""
        self._follow_up.enqueue(message)

    def next_turn(self, message: Message) -> None:
        """Queue context for the *start of the next turn*.

        The third queue, and the one whose purpose is easiest to conflate with
        the other two. Steering interrupts work in flight; a follow-up
        restarts work that finished; this adds material to a turn that was
        going to happen anyway and implies neither. A memory recall, a lint
        result, a file that changed on disk -- all belong here, because none
        of them is a correction and none should manufacture an extra turn.
        """
        self._next_turn.enqueue(message)

    def deliver_background_notice(self, message: Message) -> None:
        """Route a background-job settlement into the right queue.

        Mid-`run` → `next_turn` (lands on the next LLM turn without
        manufacturing one). Idle → `follow_up` (restarts after a finished
        run, or waits for the next `run` to drain).
        """
        with self._delivery_lock:
            if self._run_active:
                self.next_turn(message)
            else:
                self.follow_up(message)

    def clear_steering_queue(self) -> None:
        self._steering.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up.clear()

    def clear_next_turn_queue(self) -> None:
        self._next_turn.clear()

    def clear_all_queues(self) -> None:
        self._steering.clear()
        self._follow_up.clear()
        self._next_turn.clear()

    def has_queued_messages(self) -> bool:
        return (self._steering.has_items() or self._follow_up.has_items()
                or self._next_turn.has_items())

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering.mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        self._steering.mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up.mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up.mode = mode

    # ---------------- runtime control ----------------

    def append_message(self, message: Message) -> None:
        """Put a message in the transcript without running a turn.

        Seeding known context -- a file the caller already read, a prior
        result -- should not cost an LLM call to get in front of the model.
        """
        self.state.append(message)
        self._persist(message)

    def set_model(self, model: str | None) -> None:
        """Change model for subsequent turns, keeping the transcript.

        The alternative is constructing a second agent, which starts empty and
        therefore does not know what the first one found -- so escalating for
        one hard turn would mean paying to rediscover the easy ones.

        Recorded in the session as provenance: otherwise the file shows turn
        12 behaving unlike turn 11 with nothing to explain why."""
        self._model = model
        if self._session and model:
            self._session.record_model_change(model)

    @property
    def model(self) -> str | None:
        """The per-session override, or ``None`` when using the LLM default."""
        return self._model

    def set_thinking_level(self, level: ThinkingLevel | None) -> None:
        self._thinking = level
        if self._session and level is not None:
            self._session.record_thinking_level_change(str(getattr(level, "value", level)))

    @property
    def thinking_level(self) -> ThinkingLevel | None:
        return self._thinking

    def set_active_tools(self, names: list[str]) -> None:
        """Narrow which tools are offered, without unregistering them.

        Fewer tools is a genuine accuracy lever -- a tool that is not offered
        cannot be misused -- and reversible, so a later phase can re-offer
        them."""
        self.tools.set_active(names)
        if self._session:
            self._session.record_active_tools_change(list(names))

    # ---------------- branching ----------------

    def mark(self) -> str | None:
        """The current session entry id, for `rewind_to` later.

        None when there is no session file: without one there is nothing to
        rewind *to*, and returning a fake id would fail confusingly later.
        """
        return self._session._current_leaf() if self._session else None

    @property
    def session_path(self) -> Path | None:
        return self._session.path if self._session else None

    def label(self, text: str) -> str:
        """Persist a named restore point at the current session leaf."""
        if not self._session:
            raise ValueError("label requires a session (set config.session_path)")
        if not text.strip():
            raise ValueError("label text must not be empty")
        return self._session.record_label(text.strip())

    def session_tree(self) -> str:
        if not self._session:
            return "[session persistence is disabled]"
        return format_tree(self._session)

    def session_status(self) -> str:
        if not self._session:
            return "[session persistence is disabled]"
        branches = leaves(self._session)
        return (f"[session: {self._session.path} · {len(self._session.entries())} entries · "
                f"{len(branches)} branch(es) · active {self.mark()[:8] if self.mark() else 'none'}]")

    def resolve_session_target(self, target: str) -> str:
        if not self._session:
            raise ValueError("rewind requires a session (set config.session_path)")
        return resolve_leaf(self._session, target)

    def rewind_to(self, entry_id: str, summarize: bool = True) -> None:
        """Continue from an earlier point, abandoning what came after.

        The abandoned turns stay on disk as a sibling branch, so a failed
        approach can still be compared against the retry rather than being
        lost -- which is the reason the session is a tree at all.

        With a summarizer configured, what that branch established is carried
        forward before the fork: the failed attempt is usually the only record
        of what does *not* work, and discarding it means the retry pays again
        to find the same walls.

        The in-memory transcript is truncated to match. Re-pointing only the
        file would leave the agent holding the very messages it was told to
        abandon, and the next request would send them.
        """
        if not self._session:
            raise ValueError("rewind_to requires a session (set config.session_path)")

        # Summarised *before* branching, while the abandoned span is still the
        # current branch -- afterwards it is a sibling and no longer reachable
        # from the leaf we are about to move to.
        summary = ""
        if summarize and self.config.summarizer is not None:
            summary = summarize_branch(
                self._session, entry_id,
                summarizer=lambda messages: self.config.summarizer(messages))

        self._session.branch_from(entry_id)
        if summary:
            self._session.record_branch_summary(summary)
            self.next_turn(UserMessage(format_branch_summary(summary)))

        self.state.messages[:] = self._session.load()

    def active_tools(self) -> list[str]:
        return self.tools.active_names()

    def compact_now(self, limit_tokens: int | None = None) -> None:
        """Compact on demand rather than waiting for the threshold.

        Mutates `state.messages` in place: the loop may hold the same list as
        its context, and rebinding here would leave the two pointing at
        different conversations.
        """
        compacted = compact(
            self.state.messages,
            limit_tokens if limit_tokens is not None else self.config.context_limit_tokens,
            summarizer=self.config.summarizer)
        if compacted == self.state.messages:
            return
        self.state.messages[:] = compacted
        if self._session:
            checkpoint = next(
                (getattr(message, "content", "") for message in compacted
                 if isinstance(message, UserMessage)
                 and getattr(message, "content", "").startswith(SUMMARY_MARKER)),
                "")
            self._session.record_compaction(str(checkpoint), messages=compacted)

    def reset(self) -> None:
        """Start a fresh conversation.

        Queues are cleared too: a steering message written for the previous
        conversation would be nonsense injected into a new one.
        """
        listeners = self.state._listeners
        self.state = AgentState(_listeners=list(listeners))
        self.clear_all_queues()

    def new_session(self) -> Path:
        """Create a Pi-style fresh session file without mutating the old one."""
        base = self.config.session_path.parent if self.config.session_path else self.policy.root
        directory = base / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"session-{stamp}-{uuid4().hex[:8]}.jsonl"
        path.touch()
        self.config.session_path = path
        self._session = Session(path)
        self.reset()
        return path

    def run(self, prompt: str) -> RunResult:
        """Seed the transcript, then hand the turns to `run_loop`.

        `Agent` owns what persists across runs -- transcript, queues,
        listeners, session file -- and `run_loop` owns what happens *within* a
        run. They were two separate loops until the shared behaviour drifted:
        each had hardening the other lacked, and the loop reached in
        production was the less capable one. Everything the inline loop used
        to do is now expressed as `LoopConfig` and hook inputs, so there is
        one implementation to fix when something is wrong with it.
        """
        with self._delivery_lock:
            self._run_active = True
        try:
            result = self._run_inner(prompt)
            return self._drain_queued_notices(result)
        finally:
            with self._delivery_lock:
                self._run_active = False

    def continue_queued(self) -> RunResult | None:
        """Run another loop pass if follow-up / promoted notices are waiting.

        Used by the REPL after a run returns while background settlements are
        already queued, and by tests. Returns None when nothing is queued.
        """
        self._promote_next_turn_to_follow_up()
        if not self._follow_up.has_items():
            return None
        # `run_loop` only drains follow-ups *after* a finished pass. A bare
        # continuation has no new user turn, so move notices onto next_turn
        # where the loop's initial pending drain will pick them up first.
        for message in self._follow_up.drain():
            self._next_turn.enqueue(message)
        with self._delivery_lock:
            self._run_active = True
        try:
            result = self._run_loop_only()
            return self._drain_queued_notices(result)
        finally:
            with self._delivery_lock:
                self._run_active = False

    def abort_owned_background(self) -> bool:
        """Cancel owned background subagent jobs and running continuables.

        Called from ESC handling alongside bash abort. Returns True if any
        work was signaled.
        """
        jobs = self.subagent_jobs.abort_all()
        cont = self.subagent_continuable.interrupt_all()
        return jobs or cont

    def _promote_next_turn_to_follow_up(self) -> None:
        """Move undrained next_turn notices onto follow_up so they restart work.

        `next_turn` only drains at turn boundaries. A final no-tool answer can
        exit the loop while settlement notices still sit on `_next_turn`.
        """
        leftover = self._next_turn.drain()
        for message in leftover:
            self._follow_up.enqueue(message)

    def _drain_queued_notices(self, result: RunResult) -> RunResult:
        """Promote leftover next_turn and keep looping while follow-ups remain."""
        for _ in range(16):
            self._promote_next_turn_to_follow_up()
            if not self._follow_up.has_items():
                return result
            for message in self._follow_up.drain():
                self._next_turn.enqueue(message)
            result = self._run_loop_only()
        return result

    def _run_inner(self, prompt: str) -> RunResult:
        state, config = self.state, self.config

        # Each run reports its own outcome. `run_loop` only defaults the stop
        # reason when it is None, so a leftover INTERRUPTED (or MAX_TURNS, or
        # STOPPED) from the previous run would survive and mislabel this one --
        # a run that finished cleanly would report as interrupted.
        state.stop_reason = None

        # Once per conversation, not once per run. The prompt is one of the
        # largest fixed blocks in the payload and is re-sent on every turn, so
        # a second copy inflates every later request -- and sits exactly where
        # the cache breakpoint goes. `reset` clears the transcript, so a fresh
        # conversation still gets one.
        if not any(isinstance(m, SystemMessage) for m in state.messages):
            if config.system_prompt:
                prompt_text = config.system_prompt
            else:
                assembler = self.prompt_assembler
                assert assembler is not None
                assembled = assembler.assemble(
                    PromptBindings(
                        cwd=config.cwd,
                        tools=config.tools_for_prompt or None,
                        skill_listing=config.skill_listing,
                    )
                )
                prompt_text = assembled.text
            system = SystemMessage(prompt_text)
            state.append(system)
            self._persist(system)

        user = UserMessage(prompt)
        state.append(user)
        self._persist(user)

        # Recall once, here: the query is what the user just asked. Searching
        # again on each agent turn would chase a moving target and re-inject
        # the same text every turn, paying for it each time.
        self._recall_into_queue(prompt)

        return self._run_loop_only()

    def _run_loop_only(self) -> RunResult:
        """Run the turn loop against the current transcript (no new user msg)."""
        state, config = self.state, self.config
        state.stop_reason = None

        # The queues are Agent state, so they reach the loop as hooks. Composed
        # with any caller-supplied hook rather than replacing it: an
        # application that installs its own `should_stop` must keep it.
        hooks = Hooks(
            before_tool_call=self.hooks._before,
            after_tool_call=self.hooks._after,
            transform_context=self.hooks._transform,
            should_stop=self.hooks._stop,
            prepare_next_turn=self.hooks._prepare,
            # next_turn drains alongside steering because both land at the
            # start of the next turn and neither creates one. Context first,
            # correction second: a correction may refer to what was injected.
            get_steering_messages=lambda: self._take(self._next_turn)
                                          + self._take(self._steering),
            get_follow_up_messages=lambda: self._take(self._follow_up),
        )

        # `messages` is `state.messages` itself, so the loop's transcript and
        # the Agent's are one object rather than two that must be kept in step.
        context = AgentContext(system_prompt="", messages=state.messages)

        extra = {
            "bash_runs": self.bash_runs,
            "llm": self.llm,
            "ask_user": self.ask_user,
            "delegation_depth": self.delegation_depth,
            "delegated": self.delegated,
            "spawn_subagent": self.subagents.start_one_shot,
            "fork_subagent": self.subagents.start_fork,
            "subagent_jobs": self.subagent_jobs,
            "subagent_continuable": self.subagent_continuable,
            # WebFetch domain preflight hits Anthropic; skip unless
            # the caller opts in (enterprise / Claude Code parity).
            "skip_web_fetch_preflight": True,
        }
        report_sink = getattr(self, "report_sink", None)
        if report_sink is not None:
            extra["report_sink"] = report_sink

        run_loop(
            llm=self.llm,
            context=context,
            tools=self.tools,
            tool_ctx=ToolContext(
                policy=self.policy,
                extra=extra,
            ),
            state=state,
            hooks=hooks,
            config=LoopConfig(
                max_turns=config.max_turns,
                model=self._model,
                thinking_level=self._thinking,
                context_limit_tokens=config.context_limit_tokens,
                summarizer=config.summarizer,
                warn_turns_remaining=config.warn_turns_remaining,
                force_answer_on_final_turn=config.force_answer_on_final_turn,
                on_message=self._persist,
                on_compaction=self._persist_compaction,
                interrupt=config.interrupt,
            ),
        )

        state.emit(Event(EventType.AGENT_END,
                         {"turns": state.turns, "stop_reason": state.stop_reason}))
        return RunResult(
            output=state.last_assistant_text(),
            stop_reason=state.stop_reason or StopReason.DONE,
            turns=state.turns,
            tool_calls=state.tool_calls,
            usage=state.usage,
            state=state,
        )

    def _recall_into_queue(self, prompt: str) -> None:
        """Look up this prompt in memory and queue what comes back.

        Via `next_turn` rather than appended directly: that queue exists for
        context which is neither a correction nor a reason to run another
        turn, and it puts the recall *after* the user's message, where it
        reads as supporting material rather than as part of the question.

        Wrapped broadly on purpose. Memory is an enhancement, so a corrupt
        index costs this run its recall and nothing else.
        """
        if self.config.memory is None:
            return
        try:
            block = self.config.memory.recall_block(prompt)
        except Exception:  # noqa: BLE001 - never fail a run over memory
            return
        if block:
            self.next_turn(UserMessage(block))
            self.state.emit(Event(EventType.MEMORY_RECALL,
                                  {"count": block.count("\n- ")}))

    def _take(self, queue: PendingMessageQueue) -> list[Message]:
        """Hand queued messages to the loop, which appends and persists them.

        Deliberately does *not* append here. The loop's `append` is the single
        write path -- adding them on this side too would double every steering
        message in the transcript.
        """
        return queue.drain()

    def _persist(self, message) -> None:
        """Passed to the loop as `on_message`, so queued steering is persisted
        like any other message -- a resumed session that omits the steering
        misrepresents what the model was actually told."""
        if self._session:
            self._session.append(message)

    def _persist_compaction(self, messages: list[Message]) -> None:
        if self._session:
            checkpoint = next(
                (getattr(message, "content", "") for message in messages
                 if isinstance(message, UserMessage)
                 and getattr(message, "content", "").startswith(SUMMARY_MARKER)),
                "")
            self._session.record_compaction(str(checkpoint), messages=messages)
