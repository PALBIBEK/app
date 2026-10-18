"""
SimpleLoopAdapter — second harness adapter (Phase 4).

Deliberately *not* Pi-shaped:
- No `.yal` bind files
- No session_start / session_flush hooks
- Explicit begin() / checkpoint() instead
- Scope is a plain dict supplied by the caller

Hot path: note() is synchronous (local buffer only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass
class _Buffered:
    type: str
    data: dict[str, Any]
    time: int | None = None


@dataclass
class SimpleLoopAdapter:
    """
    Minimal custom loop adapter against Memory Service HTTP.

    Typical usage::

        adapter = SimpleLoopAdapter(base_url=..., scope={...}, client=httpx_client)
        await adapter.begin()
        adapter.note("user/message", {"text": "hi"})   # sync
        await adapter.checkpoint()                     # durable
        transcript = await adapter.resume()
    """

    base_url: str
    scope: dict[str, str]
    client: Any  # httpx.AsyncClient | Starlette-compatible async client wrapper
    session_id: str | None = None
    lease_owner: str | None = None
    _fence: int | None = field(default=None, init=False, repr=False)
    _pending: list[_Buffered] = field(default_factory=list, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        for key in ("tenant", "subject", "namespace"):
            if not self.scope.get(key):
                raise ValueError(f"SCOPE_REQUIRED: missing {key}")

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def note(self, type: str, data: dict[str, Any] | None = None, *, time_ms: int | None = None) -> None:
        """Synchronous — must not perform I/O."""
        self._pending.append(_Buffered(type=type, data=dict(data or {}), time=time_ms))

    async def begin(self) -> str:
        """Create or attach a memory session (awaited — not hot path)."""
        if self.session_id:
            try:
                await self._get(f"/v1/sessions/{self.session_id}")
                if self.lease_owner:
                    lease = await self._post(
                        f"/v1/sessions/{self.session_id}/lease",
                        {"owner": self.lease_owner},
                    )
                    self._fence = int(lease["fence"])
                self._started = True
                return self.session_id
            except Exception:
                pass  # create below with fixed id

        body: dict[str, Any] = {"scope": self.scope}
        if self.session_id:
            body["session_id"] = self.session_id
        res = await self._post("/v1/sessions", body)
        self.session_id = str(res["id"])
        if self.lease_owner:
            lease = await self._post(
                f"/v1/sessions/{self.session_id}/lease",
                {"owner": self.lease_owner},
            )
            self._fence = int(lease["fence"])
        self._started = True
        return self.session_id

    async def checkpoint(self) -> dict[str, Any]:
        """Durability barrier — buffer + flush over HTTP."""
        if not self._started or not self.session_id:
            raise RuntimeError("SimpleLoopAdapter: call begin() before checkpoint()")
        if not self._pending:
            return {"flushed": 0, "last_seq": -1}
        events = []
        for ev in self._pending:
            item: dict[str, Any] = {"type": ev.type, "data": ev.data}
            if ev.time is not None:
                item["time"] = ev.time
            events.append(item)
        await self._post(f"/v1/sessions/{self.session_id}/events:buffer", {"events": events})
        flush_body: dict[str, Any] = {"fence": self._fence}
        result = await self._post(f"/v1/sessions/{self.session_id}/flush", flush_body)
        flushed = len(self._pending)
        self._pending.clear()
        return {"flushed": flushed, "last_seq": result.get("last_seq", -1)}

    async def resume(self) -> dict[str, Any]:
        """Load repaired transcript from Memory Service."""
        if not self.session_id:
            await self.begin()
        data = await self._get(f"/v1/sessions/{self.session_id}")
        self._started = True
        return data

    async def recall(
        self,
        query: str,
        *,
        max_tokens: int = 256,
        log: bool = True,
    ) -> dict[str, Any]:
        """Tier 2 recall via HTTP — no Pi bind required."""
        if not self.session_id:
            await self.begin()
        body: dict[str, Any] = {
            "scope": self.scope,
            "query": query,
            "max_tokens": max_tokens,
        }
        if log:
            body["log_session_id"] = self.session_id
        return await self._post("/v1/memory/recall", body)

    async def write_fact(self, content: str, *, confidence: float = 0.85) -> str:
        res = await self._post(
            "/v1/memory/items",
            {
                "scope": self.scope,
                "content": content,
                "confidence": confidence,
                "source_session": self.session_id,
            },
        )
        return str(res["id"])

    async def _post(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        client = self.client
        # httpx.AsyncClient
        if hasattr(client, "post") and hasattr(client, "base_url"):
            r = await client.post(path, json=json_body)
            r.raise_for_status()
            return r.json()
        # Starlette TestClient is sync — wrap via run_in? Prefer AsyncClient.
        if hasattr(client, "post"):
            r = client.post(path, json=json_body)
            if hasattr(r, "raise_for_status"):
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
            return r.json()
        raise TypeError("client must support post()")

    async def _get(self, path: str) -> dict[str, Any]:
        client = self.client
        if hasattr(client, "get") and hasattr(client, "base_url"):
            r = await client.get(path)
            r.raise_for_status()
            return r.json()
        if hasattr(client, "get"):
            r = client.get(path)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
            return r.json()
        raise TypeError("client must support get()")
