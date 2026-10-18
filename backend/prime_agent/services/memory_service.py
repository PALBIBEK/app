"""Outer Memory BFF service — proxies Durable Memory (D7 FULL-PROD).

Product metaphor → AI Scope mapping (honest, not a parallel store library)
-----------------------------------------------------------------------
* ``tenant``    = ``TenantPrincipal.tenant_id`` (= user.id MVP)
* ``subject``   = authenticated user.id (owner / privacy boundary)
* ``namespace`` = ``facts`` (product Tier-2 bank partition)

List/search → ``POST /v1/memory/recall``; forget → ``/v1/memory/forget``;
erase_subject → ``/v1/memory/erase_subject``; create → ``/v1/memory/items``.
Browser never talks to Memory Service; this BFF mints a service JWT with
``tenant_id`` from the principal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from prime_agent.config import Settings, get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.memory import (
    DEFAULT_MEMORY_NAMESPACE,
    MemoryCreateRequest,
    MemoryDetailResponse,
    MemoryEntryOut,
    MemoryEraseSubjectRequest,
    MemoryEraseSubjectResponse,
    MemoryForgetRequest,
    MemoryForgetResponse,
    MemoryListResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryStatsResponse,
    MemoryUpdateRequest,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError


def raise_memory_upstream_http(exc: AiUpstreamError) -> None:
    code = exc.status_code or 503
    if code < 400:
        code = 503
    raise HTTPException(
        status_code=code if code != 401 else 502,
        detail=exc.to_dict(),
    ) from exc


class MemoryService:
    """Owner-scoped Durable Memory proxy."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AiClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AiClient.from_settings(self._settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> MemoryService:
        return cls(settings)

    @staticmethod
    def owner_scope(
        identity: TenantPrincipal,
        *,
        namespace: str | None = None,
    ) -> tuple[str, str, str]:
        """Force tenant+subject from principal; never trust client tenancy."""
        ns = (namespace or DEFAULT_MEMORY_NAMESPACE).strip() or DEFAULT_MEMORY_NAMESPACE
        return identity.tenant_id, identity.principal_id, ns

    def _entry_from_durable(
        self,
        raw: dict[str, Any],
        *,
        fallback_type: str = "long_term",
        fallback_source: str = "system",
    ) -> MemoryEntryOut:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        confidence = raw.get("confidence")
        relevance = float(confidence) if isinstance(confidence, (int, float)) else None
        return MemoryEntryOut(
            id=str(raw.get("id") or ""),
            content=str(raw.get("content") or ""),
            type=fallback_type,  # type: ignore[arg-type]
            source=fallback_source,  # type: ignore[arg-type]
            session_id=raw.get("source_session") or None,
            harness_id=None,
            metadata={"scope": raw.get("scope")} if raw.get("scope") else {},
            relevance_score=relevance,
            created_at=str(raw.get("created_at") or now),
            expires_at=raw.get("expires_at"),
        )

    async def list_items(
        self,
        identity: TenantPrincipal,
        *,
        page: int = 1,
        page_size: int = 50,
        query: str = "*",
        namespace: str | None = None,
    ) -> MemoryListResponse:
        tenant, subject, ns = self.owner_scope(identity, namespace=namespace)
        try:
            raw = await self._client.list_memory(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                query=query,
                max_tokens=max(512, page_size * 64),
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)

        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            items = []
        entries = [
            self._entry_from_durable(i) for i in items if isinstance(i, dict) and i.get("id")
        ]
        total = len(entries)
        start = max(0, (page - 1) * page_size)
        page_slice = entries[start : start + page_size]
        return MemoryListResponse(
            data=page_slice,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def search(
        self,
        identity: TenantPrincipal,
        body: MemorySearchRequest,
    ) -> MemorySearchResponse:
        tenant, subject, ns = self.owner_scope(identity, namespace=body.namespace)
        limit = body.limit or 20
        try:
            raw = await self._client.search_memory(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                query=body.query,
                max_tokens=max(256, limit * 64),
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)

        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            items = []
        results = [
            self._entry_from_durable(i) for i in items if isinstance(i, dict) and i.get("id")
        ]
        if body.min_relevance is not None:
            results = [
                r
                for r in results
                if r.relevance_score is None or r.relevance_score >= body.min_relevance
            ]
        results = results[:limit]
        return MemorySearchResponse(
            results=results,
            query=body.query,
            total_found=len(results),
        )

    async def get_item(
        self, identity: TenantPrincipal, item_id: str
    ) -> MemoryDetailResponse:
        try:
            raw = await self._client.get_memory_item(identity, item_id)
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)
        if not isinstance(raw, dict) or not raw.get("id"):
            raise HTTPException(status_code=404, detail="Memory item not found")
        # Ownership: subject must match caller when present on the item.
        scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        subject = str(scope.get("subject") or "")
        if subject and subject != identity.principal_id:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return MemoryDetailResponse(data=self._entry_from_durable(raw))

    async def create(
        self, identity: TenantPrincipal, body: MemoryCreateRequest
    ) -> MemoryDetailResponse:
        tenant, subject, ns = self.owner_scope(identity, namespace=body.namespace)
        try:
            written = await self._client.write_memory_item(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                content=body.content,
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)
        item_id = str((written or {}).get("id") or "")
        if not item_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Memory write returned no id",
            )
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        entry = MemoryEntryOut(
            id=item_id,
            content=body.content,
            type=body.type,
            source=body.source,
            session_id=body.session_id,
            harness_id=body.harness_id,
            metadata=dict(body.metadata or {}),
            created_at=now,
            expires_at=body.expires_at,
        )
        return MemoryDetailResponse(data=entry)

    async def update(
        self,
        identity: TenantPrincipal,
        item_id: str,
        body: MemoryUpdateRequest,
    ) -> MemoryDetailResponse:
        # Ensure ownership via get first.
        current = await self.get_item(identity, item_id)
        content = body.content if body.content is not None else current.data.content
        tenant, subject, ns = self.owner_scope(identity, namespace=body.namespace)
        try:
            # Supersede via write + forget old when content changes; durable supersede
            # path is available but write+forget keeps AiClient surface small.
            written = await self._client.write_memory_item(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                content=content,
            )
            await self._client.forget_memory(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                item_id=item_id,
                actor=identity.principal_id,
                reason="bff_update_supersede",
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)
        new_id = str((written or {}).get("id") or item_id)
        meta = dict(body.metadata) if body.metadata is not None else dict(current.data.metadata)
        entry = current.data.model_copy(
            update={
                "id": new_id,
                "content": content,
                "metadata": meta,
                "expires_at": body.expires_at
                if body.expires_at is not None
                else current.data.expires_at,
            }
        )
        return MemoryDetailResponse(data=entry)

    async def delete(self, identity: TenantPrincipal, item_id: str) -> None:
        tenant, subject, ns = self.owner_scope(identity)
        try:
            await self._client.forget_memory(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                item_id=item_id,
                actor=identity.principal_id,
                reason="bff_delete",
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)

    async def forget(
        self, identity: TenantPrincipal, body: MemoryForgetRequest
    ) -> MemoryForgetResponse:
        tenant, subject, ns = self.owner_scope(identity, namespace=body.namespace)
        item_id = body.item_id
        if not item_id and isinstance(body.selector, dict):
            raw_id = body.selector.get("item_id")
            if isinstance(raw_id, str) and raw_id:
                item_id = raw_id
        actor = body.actor or identity.principal_id
        try:
            raw = await self._client.forget_memory(
                identity,
                tenant=tenant,
                subject=subject,
                namespace=ns,
                item_id=item_id,
                all_in_scope=body.all_in_scope,
                actor=actor,
                reason=body.reason or "user_request",
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)
        forgotten = 0
        if isinstance(raw, dict) and isinstance(raw.get("forgotten"), int):
            forgotten = raw["forgotten"]
        return MemoryForgetResponse(forgotten=forgotten)

    async def erase_subject(
        self, identity: TenantPrincipal, body: MemoryEraseSubjectRequest
    ) -> MemoryEraseSubjectResponse:
        tenant, subject, _ns = self.owner_scope(identity)
        actor = body.actor or identity.principal_id
        try:
            raw = await self._client.erase_subject_memory(
                identity,
                tenant=tenant,
                subject=subject,
                actor=actor,
                reason=body.reason or "user_request",
            )
        except AiUpstreamError as exc:
            raise_memory_upstream_http(exc)
        return MemoryEraseSubjectResponse(
            result=raw if isinstance(raw, dict) else {"raw": raw}
        )

    async def stats(self, identity: TenantPrincipal) -> MemoryStatsResponse:
        listed = await self.list_items(identity, page=1, page_size=100)
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for item in listed.data:
            by_type[item.type] = by_type.get(item.type, 0) + 1
            by_source[item.source] = by_source.get(item.source, 0) + 1
        return MemoryStatsResponse(
            total_entries=listed.total,
            by_type=by_type,
            by_source=by_source,
            storage_used_bytes=0,
        )
