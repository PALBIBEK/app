"""Deterministic model and tool behaviour for githi turns under test.

The scripted gateway's answer is a pure function of the conversation it is
given, so it behaves the same after an activity retry and on any worker.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from supervisor.harness import HarnessCatalog, HarnessManifest, LoopSpec
from supervisor.harness.core.enums import ApprovalOnTimeout, ToolClass
from supervisor.harness.core.types import ApprovalSpec, BudgetSpec
from supervisor.seams.compaction.core import COMPACTION_NOTICE, is_summary
from supervisor.seams.core import factory
from supervisor.seams.core.types import ChatMessage, Completion, ModelGatewayError
from supervisor.seams.interfaces.subagent_transport import SubagentTransportImpl
from supervisor.seams.providers.tools import RegistryToolProvider
from supervisor.session.children import SUBAGENT_BRIEF


def _compacted(text: str) -> bool:
    """A compaction's summary (or notice) stands in for turns already answered or in
    progress; it is not a new request, so the scripted model carries on rather than restart."""
    return is_summary(text) or text.startswith(COMPACTION_NOTICE[:40])


def _notice(text: str) -> bool:
    """A note the supervisor adds for the model (a sandbox rebuilt, a background subagent
    settled), not the user's request."""
    return text.startswith(("[The sandbox was rebuilt", "Background subagent "))


@dataclasses.dataclass
class ScriptedGateway:
    """Asks for ``calls`` on a fresh user message; answers once every call has a result.

    ``vary_args`` makes each step ask again with new arguments (never answers);
    ``repeat`` makes each step ask again with the same call (a loop).
    """

    calls: list[tuple[str, dict[str, Any]]]
    cost_per_call: float = 0.0
    fail_first: int = 0
    # Raised by the agent's first calls, in order (a provider's 429, 503, 401 ...).
    errors_first: list[Exception] = dataclasses.field(default_factory=list)
    # Raised by the first summary calls of compactions, in order.
    summary_errors: list[Exception] = dataclasses.field(default_factory=list)
    vary_args: bool = False
    repeat: bool = False
    # The first step's reply is cut off by the output limit (finish_reason "length"),
    # or carries arguments that are not JSON.
    truncate_first: bool = False
    malformed_first: bool = False
    # Answers to memory's one-shot prompts (extraction, classification), kept out of `seen`.
    extract: str = "NONE"
    sensitivity: str = "ordinary"
    # What the gateway reports as the model's context window (ModelCatalog); None: unknown.
    window: int | None = None
    # Agent calls (0-based, in order, tools offered) the provider refuses as too long for its window.
    overflow_calls: set[int] = dataclasses.field(default_factory=set)
    seen: list[list[ChatMessage]] = dataclasses.field(default_factory=list)
    tools_seen: list[list[dict[str, Any]]] = dataclasses.field(default_factory=list)
    # A background subagent working in a borrowed hand (``temporal.hand_jobs``): what it
    # asks for, its answer, and an event it waits on before its first call.
    child_calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    child_answer: str = "the job is done"
    child_gate: threading.Event | None = None
    # The job's model call after this many tool results fails once (its worker is lost:
    # the job is retried); every request the job made, in order.
    child_fail_after: int | None = None
    child_seen: list[list[ChatMessage]] = dataclasses.field(default_factory=list)
    # A subagent session (its system prompt carries SUBAGENT_BRIEF): what it asks for, one
    # call per step, then its answer; an event each of its model calls waits on; and every
    # request a subagent made, in order.
    subagent_calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    subagent_answer: str = "the subagent's answer"
    subagent_gate: threading.Event | None = None
    subagent_seen: list[list[ChatMessage]] = dataclasses.field(default_factory=list)
    one_shots: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    tool_choices: list[str | None] = dataclasses.field(default_factory=list)
    # Ask for ``calls`` one per step, in order, then answer (a multi-step run).
    one_at_a_time: bool = False
    # The answer streamed to ``on_delta`` first, a chunk every ``stream_pause`` seconds.
    stream_chunks: list[str] = dataclasses.field(default_factory=list)
    stream_pause: float = 0.0
    # Raised once the chunks have streamed: the call fails part way through its reply.
    stream_error: Exception | None = None
    # The session key the supervisor gave it (``rekey``), and keys the gateway no longer
    # takes: a call made with one is refused as an unknown key (401 AUTH_INVALID).
    token: str | None = None
    refused_tokens: set[str] = dataclasses.field(default_factory=set)
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def rekey(self, token: str) -> None:
        self.token = token

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        tool_choice: str | None = None,
        on_delta: Any = None,
    ) -> Completion:
        if self.token is not None and self.token in self.refused_tokens:
            raise ModelGatewayError(401, '{"detail":{"error":{"code":"AUTH_INVALID","message":"unknown virtual key"}}}')
        self.tool_choices.append(tool_choice)
        if self.stream_chunks and on_delta is not None and tools:
            for chunk in self.stream_chunks:
                on_delta("text", chunk)
                time.sleep(self.stream_pause)
            if self.stream_error is not None:
                error, self.stream_error = self.stream_error, None
                raise error
            return Completion(ChatMessage("assistant", "".join(self.stream_chunks)), model="scripted", usage={})
        if tool_choice == "none":  # the model is told to answer: it does
            with self._lock:
                self.seen.append(list(messages))
                self.tools_seen.append(list(tools))
            results = sum(1 for m in messages if m.role == "tool")
            return Completion(
                ChatMessage("assistant", f"final answer after {results} tool results"),
                model="scripted",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call},
            )
        if (answer := self._one_shot(messages)) is not None:
            return Completion(ChatMessage("assistant", answer), model="scripted", usage={})
        if messages and messages[0].content.startswith("You are a background subagent"):
            return self._child(messages)
        if any(m.role == "system" and m.content == SUBAGENT_BRIEF for m in messages):
            return self._subagent(messages)
        with self._lock:
            self.seen.append(list(messages))
            self.tools_seen.append(list(tools))
            summarizing = bool(messages) and messages[0].content.startswith("You are a context summarization")
            if self.errors_first and not summarizing:
                raise self.errors_first.pop(0)
            if self.summary_errors and summarizing:
                raise self.summary_errors.pop(0)
            if summarizing:  # a summarizer writes a summary (never asks for tools)
                return Completion(
                    ChatMessage("assistant", "## Goal\nCarry on with the user's request (scripted summary)."),
                    model="scripted",
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call},
                )
            if self.fail_first > 0:
                self.fail_first -= 1
                raise ConnectionError("model gateway unavailable (scripted)")
            # Counted over the agent's calls only: compaction's summary request offers no tools.
            if tools and sum(1 for t in self.tools_seen if t) - 1 in self.overflow_calls:
                raise RuntimeError(
                    "model gateway error 400: This model's maximum context length is 2000 tokens"
                )
        results = sum(1 for m in messages if m.role == "tool")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call}
        if self.one_at_a_time:
            return self._next_of(messages, usage)
        fresh = (
            messages[-1].role == "user"
            and not _compacted(messages[-1].content)
            and not _notice(messages[-1].content)
        )
        asking = bool(self.calls) and (fresh or self.repeat or self.vary_args)
        if not asking:
            text = f"done after {results} tool results"
            return Completion(ChatMessage("assistant", text), model="scripted", usage=usage)
        calls = [
            {
                "id": f"call-{results}-{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": (
                        '{"path": "."'  # cut off mid-object
                        if self.malformed_first and results == 0
                        else json.dumps(args | ({"n": results} if self.vary_args else {}))
                    ),
                },
            }
            for i, (name, args) in enumerate(self.calls)
        ]
        message = ChatMessage("assistant", "", tool_calls=calls)
        finish = "length" if self.truncate_first and results == 0 else "tool_calls"
        return Completion(
            message, model="scripted", usage=usage, tool_calls=calls, finish_reason=finish
        )

    def _next_of(self, messages: list[ChatMessage], usage: dict[str, Any]) -> Completion:
        """``calls[k]`` where k is the tool results since the last user message; then answer."""
        last_user = max(
            (i for i, m in enumerate(messages) if m.role == "user" and not _compacted(m.content) and not _notice(m.content)),
            default=-1,
        )
        since = messages[last_user + 1 :]
        # As a model told its sandbox was rebuilt would: redo each lost step, once.
        asked = {c.get("id") for m in since for c in (m.tool_calls or [])}
        lost = [int(n) for m in since if m.role == "user" and _notice(m.content)
                for n in re.findall(r"\(step-(\d+)\)", m.content)]
        for n in lost:
            if f"redo-{n}" not in asked:
                name, args = self.calls[n]
                calls = [{"id": f"redo-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
                return Completion(ChatMessage("assistant", "", tool_calls=calls), model="scripted", usage=usage,
                                  tool_calls=calls, finish_reason="tool_calls")
        done = sum(1 for m in since if m.role == "tool" and not str(m.tool_call_id or "").startswith("redo-"))
        if done >= len(self.calls):
            return Completion(ChatMessage("assistant", f"done after {done} steps"), model="scripted", usage=usage)
        name, args = self.calls[done]
        calls = [{"id": f"step-{done}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
        return Completion(ChatMessage("assistant", "", tool_calls=calls), model="scripted", usage=usage,
                          tool_calls=calls, finish_reason="tool_calls")

    def _subagent(self, messages: list[ChatMessage]) -> Completion:
        if self.subagent_gate is not None and not self.subagent_gate.wait(timeout=60):
            raise TimeoutError("the subagent's gate was never opened")
        with self._lock:
            self.subagent_seen.append(list(messages))
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call}
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        done = sum(1 for m in messages[last_user + 1 :] if m.role == "tool")
        if done < len(self.subagent_calls):
            name, args = self.subagent_calls[done]
            calls = [{"id": f"sub-{done}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}}]
            return Completion(ChatMessage("assistant", "", tool_calls=calls), model="scripted",
                              usage=usage, tool_calls=calls, finish_reason="tool_calls")
        return Completion(ChatMessage("assistant", self.subagent_answer), model="scripted", usage=usage)

    def _child(self, messages: list[ChatMessage]) -> Completion:
        if self.child_gate is not None and not self.child_gate.wait(timeout=60):
            raise TimeoutError("the child's gate was never opened")
        done = sum(1 for m in messages if m.role == "tool")
        with self._lock:
            self.child_seen.append(list(messages))
            if self.child_fail_after == done:
                self.child_fail_after = None
                raise ConnectionError("the job's worker was lost (scripted)")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call}
        if done < len(self.child_calls):
            name, args = self.child_calls[done]
            calls = [{"id": f"child-{done}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}}]
            return Completion(ChatMessage("assistant", "", tool_calls=calls), model="scripted",
                              usage=usage, tool_calls=calls)
        return Completion(ChatMessage("assistant", self.child_answer), model="scripted", usage=usage)

    def _one_shot(self, messages: list[ChatMessage]) -> str | None:
        system = messages[0].content if messages and messages[0].role == "system" else ""
        if system.startswith("You extract durable facts"):
            answer = self.extract
        elif system.startswith("Classify sensitivity"):
            answer = self.sensitivity
        else:
            return None
        with self._lock:
            self.one_shots.append((system.split("\n", 1)[0], messages[-1].content))
        return answer

    def context_window(self, model: str) -> int | None:
        return self.window

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:  # pragma: no cover
        raise NotImplementedError


@contextmanager
def scripted_model(gateway: ScriptedGateway) -> Iterator[ScriptedGateway]:
    """Every githi session built while active uses ``gateway`` as its model."""
    original = factory._resolve_gateway
    factory._resolve_gateway = lambda **_: gateway  # type: ignore[assignment]
    try:
        yield gateway
    finally:
        factory._resolve_gateway = original


@contextmanager
def slow_tool(name: str, seconds: float) -> Iterator[None]:
    """Make one catalog tool take ``seconds`` (in the worker's thread)."""
    original = RegistryToolProvider.invoke

    def invoke(self: RegistryToolProvider, ctx: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == name:
            time.sleep(seconds)
        return original(self, ctx, tool, args)

    RegistryToolProvider.invoke = invoke  # type: ignore[method-assign]
    try:
        yield
    finally:
        RegistryToolProvider.invoke = original  # type: ignore[method-assign]


@contextmanager
def slow_end_run(seconds: float) -> Iterator[None]:
    """Make closing a run (session.end_run) wait ``seconds`` before it does anything."""
    from supervisor.temporal.steps import GithiSteps

    original = GithiSteps.end_run

    async def end_run(self: GithiSteps, req: Any) -> Any:
        await asyncio.sleep(seconds)
        return await original(self, req)

    GithiSteps.end_run = end_run  # type: ignore[method-assign]
    try:
        yield
    finally:
        GithiSteps.end_run = original  # type: ignore[method-assign]


@contextmanager
def slow_subagent(seconds: float) -> Iterator[None]:
    """Make every subagent run (any provider) take ``seconds`` (in the caller's thread)."""
    original = SubagentTransportImpl.start_one_shot

    def start_one_shot(self: SubagentTransportImpl, provider: str, request: Any) -> Any:
        time.sleep(seconds)
        return original(self, provider, request)

    SubagentTransportImpl.start_one_shot = start_one_shot  # type: ignore[method-assign]
    try:
        yield
    finally:
        SubagentTransportImpl.start_one_shot = original  # type: ignore[method-assign]


def githi_variant(catalog: HarnessCatalog, version: str, **changes: Any) -> HarnessManifest:
    """Register a githi-v1 copy under ``version`` with manifest changes applied."""
    manifest = dataclasses.replace(
        catalog.resolve_sync("githi-v1"), harness_version=version, **changes
    )
    catalog.register_sync(manifest)
    return manifest


def approval_variants(catalog: HarnessCatalog) -> None:
    """githi with ``write`` gated by a human (2s timeout), denying or allowing on timeout."""
    for on_timeout in ApprovalOnTimeout:
        githi_variant(
            catalog,
            f"githi-approve-{on_timeout}",
            approvals=ApprovalSpec(
                timeout_s=2.0, on_timeout=on_timeout, require_approval=("write",)
            ),
        )


def once_variant(catalog: HarnessCatalog) -> None:
    """githi where ``write`` is non-atomic: run at most once, never retried."""
    base = catalog.resolve_sync("githi-v1")
    recovery = dataclasses.replace(
        base.recovery, tool_classes=base.recovery.tool_classes | {"write": ToolClass.NON_ATOMIC}
    )
    githi_variant(catalog, "githi-once", recovery=recovery)


def budget_variant(catalog: HarnessCatalog) -> None:
    budget = BudgetSpec(max_cost_per_session=0.01, max_tokens_per_turn=None)
    githi_variant(catalog, "githi-budget", budget=budget)
    # Each scripted model call reports 15 tokens: a run past 20, a session past 40.
    githi_variant(catalog, "githi-run-tokens", budget=BudgetSpec(max_tokens_per_turn=20))
    githi_variant(catalog, "githi-session-tokens", budget=BudgetSpec(max_tokens_per_session=40))
    githi_variant(catalog, "githi-session-time", budget=BudgetSpec(max_seconds_per_session=2.0))


def loop_variant(catalog: HarnessCatalog) -> None:
    """githi with a 1 s run time limit (``githi-loop``), or one quick model retry
    (``githi-retry``), pinned in ``loop:``."""
    base = catalog.resolve_sync("githi-v1").loop or LoopSpec()
    githi_variant(catalog, "githi-loop", loop=dataclasses.replace(base, max_run_seconds=1.0))
    githi_variant(catalog, "githi-deadline", loop=dataclasses.replace(base, hard_run_seconds=2.0))
    # Two steps a run and no forced answer on the last: a run that asks twice hits the limit.
    githi_variant(
        catalog, "githi-short", loop=dataclasses.replace(base, max_steps=2, force_answer_on_final_step=False)
    )
    githi_variant(
        catalog,
        "githi-retry",
        loop=dataclasses.replace(base, model_retries=1, retry_base_seconds=0.1, retry_max_seconds=1.0),
    )


def all_variants(catalog: HarnessCatalog) -> HarnessCatalog:
    approval_variants(catalog)
    once_variant(catalog)
    budget_variant(catalog)
    loop_variant(catalog)
    return catalog


@contextmanager
def huge_result(name: str, chars: int) -> Iterator[None]:
    """Make one catalog tool answer ``chars`` characters, as a tool that does not bound
    its own output (an MCP server's) may."""
    from supervisor.seams.core.types import ToolResult

    original = RegistryToolProvider.invoke

    def invoke(self: RegistryToolProvider, ctx: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == name:
            return ToolResult(ok=True, payload="head " + "x" * chars + " tail")
        return original(self, ctx, tool, args)

    RegistryToolProvider.invoke = invoke  # type: ignore[method-assign]
    try:
        yield
    finally:
        RegistryToolProvider.invoke = original  # type: ignore[method-assign]
