"""Long-term memory through the supervisor's notebooks.

Notebooks belong to one user and one exact agent version, the ``agent_pin`` a session
of a bound harness runs as (``composition:<name>@<digest>``). The backend resolves the
pin from a harness the user owns; the supervisor checks the notebook is the caller's.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.harness_service import HarnessService


def agent_pin(composition_id: str, composition_digest: str) -> str:
    """The pin the supervisor gives sessions of this composition (``memory.binding.agent_pin``)."""
    return f"composition:{composition_id}@{composition_digest}"


def raise_memory_http(exc: AiUpstreamError) -> None:
    """The supervisor's status and reason: 404 for a notebook that is not the caller's."""
    code = exc.status_code or 503
    raise HTTPException(
        status_code=code,
        detail={"error_code": "MEMORY_UNAVAILABLE" if code >= 500 else "MEMORY_ERROR", "message": str(exc)},
    ) from exc


class MemoryService:
    def __init__(self, client: AiClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MemoryService:
        return cls(AiClient.from_settings(settings))

    async def pin_for(self, harness_id: str, user_id: str, db: AsyncSession) -> str:
        harness = await HarnessService.get_owned(harness_id, user_id, db)
        if harness.bind_status != "bound" or not harness.composition_id or not harness.composition_digest:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error_code": "NOT_BOUND", "message": "harness is not bound: it has no memory yet"},
            )
        return agent_pin(harness.composition_id, harness.composition_digest)

    async def call(self, coro: Any) -> Any:
        try:
            return await coro
        except AiUpstreamError as exc:
            raise_memory_http(exc)

    async def list_notebooks(
        self, identity: TenantPrincipal, pin: str, *, archived: bool
    ) -> list[dict[str, Any]]:
        return await self.call(self._client.list_notebooks(identity, agent_pin=pin, archived=archived))

    async def create_notebook(
        self, identity: TenantPrincipal, pin: str, name: str, label: str | None
    ) -> dict[str, Any]:
        return await self.call(self._client.create_notebook(identity, agent_pin=pin, name=name, label=label))

    async def notebook(
        self,
        identity: TenantPrincipal,
        method: str,
        notebook_id: str,
        sub: str = "",
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return await self.call(
            self._client.notebook_request(
                identity, method, notebook_id, sub, json_body=json_body, expected=expected
            )
        )

    async def search(
        self, identity: TenantPrincipal, pin: str, query: str, notebook_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return await self.call(
            self._client.search_notebooks(
                identity, agent_pin=pin, query=query, notebook_id=notebook_id, limit=limit
            )
        )
