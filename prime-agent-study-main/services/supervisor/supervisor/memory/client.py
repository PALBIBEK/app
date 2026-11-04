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

A circuit breaker keeps a failing memory service from slowing every turn: after
``_Breaker.threshold`` failures in a row (unreachable, timed out, or a 5xx) calls fail
at once with ``MemoryUnavailable`` for ``cooldown_s``; then one call tries again, and
its success closes the breaker. Turns already treat ``MemoryUnavailable`` as "no
memory this turn", so a degraded service costs a turn nothing instead of a timeout
per call.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from supervisor.memory_service.notebooks import wire
from supervisor.memory_service.notebooks.errors import (
    MemoryFactsMissing,
    MemoryLimitExceeded,
    NotebookError,
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
    RevisionOutOfRange,
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
_CONNECT_TIMEOUT_S = 3.0

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


class _Breaker:
    """Closed, open (fail fast until ``cooldown_s`` passes), then half-open (one trial)."""

    def __init__(
        self, *, threshold: int = 5, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold, self.cooldown_s, self._clock = threshold, cooldown_s, clock
        self._failures = 0
        self._open_until: float | None = None
        self._trial = False

    def before(self) -> bool:
        """Raise ``MemoryUnavailable`` when the call should not be made; True for the trial."""
        if self._open_until is None:
            return False
        wait = self._open_until - self._clock()
        if wait > 0 or self._trial:
            raise MemoryUnavailable(
                f"memory service is failing; calls are skipped for {max(wait, 0.0):.0f}s more"
            )
        self._trial = True  # half-open: this call is the one trial
        return True

    def succeeded(self) -> None:
        self._failures, self._open_until, self._trial = 0, None, False

    def failed(self) -> None:
        self._failures += 1
        if self._trial or self._failures >= self.threshold:
            if self._open_until is None or self._trial:
                logger.warning("memory service failing: skipping calls for %.0fs", self.cooldown_s)
            self._open_until = self._clock() + self.cooldown_s
        self._trial = False

    def abandoned(self, trial: bool) -> None:
        """The call was cancelled: it says nothing of the service (if it was the trial, the
        next call is)."""
        if trial:
            self._trial = False


class NotebookClient:
    """Notebooks and their facts, through the memory service."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Credential = _default_credential,
        timeout_s: float = _TIMEOUT_S,
        breaker: _Breaker | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=httpx.Timeout(timeout_s, connect=min(_CONNECT_TIMEOUT_S, timeout_s)),
        )
        self._credential = credential
        self._breaker = breaker or _Breaker()

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

    async def history(self, notebook_id: str, *, page: int = 5_000) -> list[Revision]:
        """Every change, oldest first, read page by page (a page holds whole revisions)."""
        out: list[Revision] = []
        after = 0
        while True:
            rows = await self.revisions(notebook_id, after=after, limit=page)
            out += rows
            if len(rows) < page:
                return out
            after = rows[-1].revision

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
        trial = self._breaker.before()
        try:
            response = await self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            self._breaker.failed()
            raise MemoryUnavailable(f"memory service unreachable: {exc}") from exc
        except BaseException:
            self._breaker.abandoned(trial)
            raise
        if response.status_code >= 500:
            self._breaker.failed()
            raise _error(response)
        if response.status_code < 400:
            self._breaker.succeeded()
            return response.json() if response.content else None
        self._breaker.succeeded()  # a refusal (4xx) is an answer: the service is up
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
    if code == "MemoryLimitExceeded":
        return MemoryLimitExceeded(
            str(detail.get("what", "")), int(detail.get("limit", -1)), int(detail.get("got", -1))
        )
    if code == "RevisionOutOfRange":
        return RevisionOutOfRange(int(detail.get("revision", -1)), int(detail.get("current", -1)))
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
