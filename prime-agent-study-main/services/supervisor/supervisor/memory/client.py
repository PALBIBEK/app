"""The supervisor's side of notebooks: a client of the memory service's notebook routes.

Notebooks are stored only in the memory service. Every operation that reads facts
and writes depending on them (capture, edit, delete, clear, restore) is one call,
so the read and the write run in one transaction where the facts are, never apart
across the network. What a caller may *see* (disclosure, classification) stays in
the supervisor, which knows audiences; the memory service never imports it.

Each request carries a credential for the notebook's tenant. A notebook id names
its tenant (``<tenant>~<hex>``), so any worker can make that call without a
lookup; the service still checks the stored tenant, so a forged prefix finds
nothing. Errors come back as the service's error classes, rebuilt from ``code``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from supervisor.memory_service.notebooks import wire
from supervisor.memory_service.notebooks.errors import (
    MemoryFactsMissing,
    NotebookError,
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Fact,
    Notebook,
    NotebookStats,
    Revision,
    subject_of,
    tenant_of,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryRefused",
    "MemoryServiceNotConfigured",
    "MemoryUnavailable",
    "NotebookClient",
    "UnavailableNotebooks",
]

_TIMEOUT_S = 15.0

Credential = Callable[[str], str | None]


class MemoryUnavailable(NotebookError):
    """The memory service could not be reached, or failed; nothing is known to have changed."""

    retryable = True


class MemoryServiceNotConfigured(NotebookError):
    """No memory service is configured, so there is no long-term memory (not retryable)."""


class MemoryRefused(NotebookError):
    """The memory service refused this supervisor's credential (a configuration error)."""


def _default_credential(tenant_id: str) -> str | None:
    from supervisor.seams.services.memory_service_client import memory_service_credential

    return memory_service_credential(tenant_id)


class NotebookClient:
    """Notebooks and their facts, through the memory service."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Credential = _default_credential,
        timeout_s: float = _TIMEOUT_S,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), transport=transport, timeout=timeout_s
        )
        self._credential = credential

    @classmethod
    def from_env(cls) -> NotebookClient | None:
        """The client for ``MEMORY_SERVICE_URL``; None when no memory service is configured."""
        url = os.environ.get("MEMORY_SERVICE_URL", "").strip()
        return cls(url) if url else None

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- notebooks -------------------------------------------------------------

    async def create(
        self,
        *,
        tenant_id: str,
        owner: str,
        agent_pin: str,
        name: str,
        label: str | None = None,
        agent: str | None = None,
    ) -> Notebook:
        key = _key(tenant_id, owner, agent_pin, name, agent=agent, label=label)
        return wire.notebook_in(await self._call(tenant_id, "POST", "/v1/notebooks", json=key))

    async def ensure(
        self, *, tenant_id: str, owner: str, agent_pin: str, name: str, agent: str | None = None
    ) -> Notebook:
        """The named notebook, created on first use (safe under concurrent callers)."""
        key = _key(tenant_id, owner, agent_pin, name, agent=agent)
        return wire.notebook_in(
            await self._call(tenant_id, "POST", "/v1/notebooks:ensure", json=key)
        )

    async def get(self, notebook_id: str) -> Notebook:
        return wire.notebook_in(await self._on(notebook_id, "GET", ""))

    async def list_notebooks(
        self, *, tenant_id: str, owner: str, agent_pin: str, archived: bool = False
    ) -> list[Notebook]:
        params = {"tenant_id": tenant_id, "owner": owner, "agent_pin": agent_pin}
        if archived:
            params["archived"] = "true"
        rows = await self._call(tenant_id, "GET", "/v1/notebooks", params=params)
        return [wire.notebook_in(r) for r in rows]

    async def set_archived(self, notebook_id: str, archived: bool) -> Notebook:
        body = await self._on(notebook_id, "POST", "/archive", json={"archived": archived})
        return wire.notebook_in(body)

    async def mark_tidied(self, notebook_id: str, at: int) -> None:
        await self._on(notebook_id, "POST", "/tidied", json={"at": at})

    async def stats(self, notebook_id: str) -> NotebookStats:
        return wire.stats_in(await self._on(notebook_id, "GET", "/stats"))

    # --- facts -----------------------------------------------------------------

    async def facts(self, notebook_id: str) -> list[Fact]:
        return [wire.fact_in(f) for f in await self._on(notebook_id, "GET", "/facts")]

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        rows = await self._on(notebook_id, "GET", "/facts", params={"at_revision": revision})
        return [wire.fact_in(f) for f in rows]

    async def revisions(
        self, notebook_id: str, *, after: int = 0, limit: int = 500
    ) -> list[Revision]:
        rows = await self._on(
            notebook_id, "GET", "/revisions", params={"after": after, "limit": limit}
        )
        return [wire.revision_in(r) for r in rows]

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> int:
        body = {"facts": list(facts), "author": author, "context": wire.context_out(context)}
        return int((await self._on(notebook_id, "POST", "/facts:capture", json=body))["added"])

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        body = {
            "edits": [wire.edit_out(e) for e in edits],
            "author": author,
            "context": wire.context_out(context),
        }
        await self._on(notebook_id, "POST", "/facts:edit", json=body)

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None:
        await self._on(notebook_id, "POST", "/facts:delete", json={"ids": list(ids), "author": author})

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        body = await self._on(notebook_id, "POST", "/facts:clear", json={"author": author})
        return int(body["cleared"])

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        await self._on(
            notebook_id, "POST", "/facts:restore", json={"revision": revision, "author": author}
        )

    # --- erasure ---------------------------------------------------------------

    async def erase_owner(self, *, tenant_id: str, owner: str, actor: str, reason: str) -> int:
        """Erase everything the memory service keeps of ``owner`` in ``tenant_id``: their
        notebooks with facts and history, and what was learnt about them. The notebooks
        removed (0 the second time)."""
        body = await self._call(
            tenant_id,
            "POST",
            "/v1/memory/erase_subject",
            json={"tenant": tenant_id, "subject": subject_of(owner), "actor": actor, "reason": reason},
        )
        return int((body or {}).get("notebooks_erased", 0))

    # --- transport -------------------------------------------------------------

    async def _on(self, notebook_id: str, method: str, suffix: str, **kw: Any) -> Any:
        tenant = tenant_of(notebook_id)
        if not tenant:
            raise NotebookNotFound(notebook_id)  # not an id this service ever issued
        return await self._call(tenant, method, f"/v1/notebooks/{notebook_id}{suffix}", **kw)

    async def _call(self, tenant_id: str, method: str, path: str, **kw: Any) -> Any:
        headers = {}
        if token := self._credential(tenant_id):
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise MemoryUnavailable(f"memory service unreachable: {exc}") from exc
        if response.status_code < 400:
            return response.json() if response.content else None
        raise _error(response)


def _key(
    tenant_id: str,
    owner: str,
    agent_pin: str,
    name: str,
    *,
    agent: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "owner": owner,
        "agent_pin": agent_pin,
        "name": name,
        "agent": agent,
        "label": label,
    }


def _error(response: httpx.Response) -> NotebookError:
    """The service's error, rebuilt from its ``code`` and details."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if not isinstance(detail, dict):
        detail = {"message": str(detail or response.text)[:500]}
    code = detail.get("code")
    if code == "NotebookNotFound":
        return NotebookNotFound(str(detail.get("ref", "")))
    if code == "NotebookExists":
        return NotebookExists(str(detail.get("name", "")))
    if code == "MemoryFactsMissing":
        return MemoryFactsMissing(tuple(str(i) for i in detail.get("ids") or ()))
    if code == "RevisionConflict":
        return RevisionConflict(int(detail.get("expected", -1)), int(detail.get("actual", -1)))
    if response.status_code in (401, 403):
        return MemoryRefused(
            f"memory service refused the request ({response.status_code}): "
            f"{detail.get('message', detail)}"
        )
    if response.status_code == 404:
        # A notebook of another tenant is "not found" too, by design.
        return NotebookNotFound(str(detail.get("message", "")))
    logger.warning("memory service refused %s: %s %s", response.request.url.path, response.status_code, detail)
    return MemoryUnavailable(
        f"memory service returned {response.status_code}: {detail.get('message', detail)}"
    )


class UnavailableNotebooks:
    """Stands in when no memory service is configured: any use says so plainly."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def unavailable(*_: Any, **__: Any) -> Any:
            raise MemoryServiceNotConfigured(
                "long-term memory needs the memory service: set MEMORY_SERVICE_URL"
            )

        return unavailable

    async def aclose(self) -> None:
        return None
