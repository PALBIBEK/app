"""Deterministic model and tool behaviour for githi turns under test.

The scripted gateway's answer is a pure function of the conversation it is
given, so it behaves the same after an activity retry and on any worker.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from supervisor.harness import HarnessCatalog, HarnessManifest
from supervisor.harness.core.enums import ApprovalOnTimeout, ToolClass
from supervisor.harness.core.types import ApprovalSpec, BudgetSpec
from supervisor.seams.core import factory
from supervisor.seams.core.types import ChatMessage, Completion
from supervisor.seams.interfaces.subagent_transport import SubagentTransportImpl
from supervisor.seams.providers.tools import RegistryToolProvider


@dataclasses.dataclass
class ScriptedGateway:
    """Asks for ``calls`` on a fresh user message; answers once every call has a result.

    ``vary_args`` makes each step ask again with new arguments (never answers);
    ``repeat`` makes each step ask again with the same call (a loop).
    """

    calls: list[tuple[str, dict[str, Any]]]
    cost_per_call: float = 0.0
    fail_first: int = 0
    vary_args: bool = False
    repeat: bool = False
    # Answers to memory's one-shot prompts (extraction, classification), kept out of `seen`.
    extract: str = "NONE"
    sensitivity: str = "ordinary"
    # What the gateway reports as the model's context window (ModelCatalog); None: unknown.
    window: int | None = None
    seen: list[list[ChatMessage]] = dataclasses.field(default_factory=list)
    tools_seen: list[list[dict[str, Any]]] = dataclasses.field(default_factory=list)
    # A background subagent working in a borrowed hand (``temporal.hand_jobs``): what it
    # asks for, its answer, and an event it waits on before its first call.
    child_calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    child_answer: str = "the job is done"
    child_gate: threading.Event | None = None
    one_shots: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def complete(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]], *, model: str | None = None
    ) -> Completion:
        if (answer := self._one_shot(messages)) is not None:
            return Completion(ChatMessage("assistant", answer), model="scripted", usage={})
        if messages and messages[0].content.startswith("You are a background subagent"):
            return self._child(messages)
        with self._lock:
            self.seen.append(list(messages))
            self.tools_seen.append(list(tools))
            if self.fail_first > 0:
                self.fail_first -= 1
                raise ConnectionError("model gateway unavailable (scripted)")
        results = sum(1 for m in messages if m.role == "tool")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call}
        asking = bool(self.calls) and (messages[-1].role == "user" or self.repeat or self.vary_args)
        if not asking:
            text = f"done after {results} tool results"
            return Completion(ChatMessage("assistant", text), model="scripted", usage=usage)
        calls = [
            {
                "id": f"call-{results}-{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args | ({"n": results} if self.vary_args else {})),
                },
            }
            for i, (name, args) in enumerate(self.calls)
        ]
        message = ChatMessage("assistant", "", tool_calls=calls)
        return Completion(message, model="scripted", usage=usage, tool_calls=calls)

    def _child(self, messages: list[ChatMessage]) -> Completion:
        if self.child_gate is not None and not self.child_gate.wait(timeout=60):
            raise TimeoutError("the child's gate was never opened")
        done = sum(1 for m in messages if m.role == "tool")
        if done < len(self.child_calls):
            name, args = self.child_calls[done]
            calls = [{"id": f"child-{done}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}}]
            return Completion(ChatMessage("assistant", "", tool_calls=calls), model="scripted",
                              usage={}, tool_calls=calls)
        return Completion(ChatMessage("assistant", self.child_answer), model="scripted", usage={})

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


def all_variants(catalog: HarnessCatalog) -> HarnessCatalog:
    approval_variants(catalog)
    once_variant(catalog)
    budget_variant(catalog)
    return catalog
