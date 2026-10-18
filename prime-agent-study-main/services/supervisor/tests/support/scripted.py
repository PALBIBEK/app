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

from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.harness import HarnessCatalog, HarnessManifest
from supervisor.harness.core.enums import ToolClass
from supervisor.harness.core.types import ApprovalSpec, BudgetSpec
from supervisor.seams.core import factory
from supervisor.seams.core.types import ChatMessage, Completion


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
    seen: list[list[ChatMessage]] = dataclasses.field(default_factory=list)
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def complete(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]], *, model: str | None = None
    ) -> Completion:
        with self._lock:
            self.seen.append(list(messages))
            if self.fail_first > 0:
                self.fail_first -= 1
                raise ConnectionError("model gateway unavailable (scripted)")
        results = sum(1 for m in messages if m.role == "tool")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_total": self.cost_per_call}
        asking = messages[-1].role == "user" or self.repeat or self.vary_args
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
    original = CatalogToolProvider.invoke

    def invoke(self: CatalogToolProvider, ctx: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == name:
            time.sleep(seconds)
        return original(self, ctx, tool, args)

    CatalogToolProvider.invoke = invoke  # type: ignore[method-assign]
    try:
        yield
    finally:
        CatalogToolProvider.invoke = original  # type: ignore[method-assign]


def githi_variant(catalog: HarnessCatalog, version: str, **changes: Any) -> HarnessManifest:
    """Register a githi-v1 copy under ``version`` with manifest changes applied."""
    manifest = dataclasses.replace(
        catalog.resolve_sync("githi-v1"), harness_version=version, **changes
    )
    catalog.register_sync(manifest)
    return manifest


def approval_variants(catalog: HarnessCatalog) -> None:
    """githi with ``write`` gated by a human (2s timeout), denying or allowing on timeout."""
    for on_timeout in ("deny", "allow"):
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
