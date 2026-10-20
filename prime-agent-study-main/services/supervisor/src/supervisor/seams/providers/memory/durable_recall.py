"""DurableRecallProvider — Tier 2 recall via Memory Service HTTP."""

from __future__ import annotations

from typing import Any, Literal

from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider
from supervisor.seams.services.memory_service_client import (
    memory_service_base_url,
    recall_memory_items,
    resolve_memory_scope,
)


class DurableRecallProvider:
    """Recall via ``POST /v1/memory/recall`` — degrades to no recall on errors."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        max_tokens: int = 512,
        timeout: float = 30.0,
        namespace: str | None = None,
    ) -> None:
        self._base_url = (
            (base_url if base_url is not None else memory_service_base_url())
            .strip()
            .rstrip("/")
        )
        self._service_token = service_token
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._namespace = namespace
        self._null = NullContextMemoryProvider()

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._null.estimate_tokens(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        return self._null.before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        query = (user_prompt or "").strip()
        if not query or not self._base_url:
            return None
        scope = resolve_memory_scope(
            session_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            namespace=self._namespace,
            owner=ctx.owner or None,
            agent=ctx.agent or None,
        )
        try:
            # No log_session_id: that names one of the memory service's own logical
            # sessions, which supervisor sessions are not (sending ours made every
            # recall a 404).
            result = recall_memory_items(
                query=query,
                scope=scope,
                max_tokens=self._max_tokens,
                base_url=self._base_url,
                service_token=self._service_token,
                timeout=self._timeout,
            )
        except Exception:  # noqa: BLE001 — degrade never fail run
            return None
        items = result.get("items") or []
        parts: list[str] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            content = row.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        if not parts:
            return None
        text = "\n".join(f"- {p}" for p in parts)
        return RecallBlock(text=text, source="semantic")

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        return self._null.append_plug_state(ctx, custom_type, data)


assert isinstance(
    DurableRecallProvider(base_url="http://127.0.0.1:8091"),
    ContextMemoryProvider,
)
