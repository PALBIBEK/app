"""A user's client for the supervisor API, as the backend (BFF) would call it.

Each call carries a service JWT for ``(tenant, principal)``. Helpers cover the
user journeys the stages exercise: catalog, sessions, turns, events, approvals.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

SUP = os.environ.get("SUP", "http://127.0.0.1:8080")
SECRET = os.environ.get("SERVICE_JWT_SECRET", "dev-local-service-jwt-secret-32b!!")
RELAY_LOG = os.environ.get("RELAY_LOG", "relay.jsonl")


def token(tenant: str = "t1", principal: str = "alice") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "prime-agent-api",
            "iss": "prime-agent-api",
            "aud": "yal-supervisor",
            "type": "service",
            "tenant_id": tenant,
            "principal_id": principal,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": uuid.uuid4().hex,
        },
        SECRET,
        algorithm="HS256",
    )


class User:
    def __init__(self, tenant: str = "t1", principal: str = "alice") -> None:
        self.tenant, self.principal = tenant, principal
        self.http = httpx.Client(base_url=SUP, timeout=120)

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {token(self.tenant, self.principal)}"}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, headers=self._h(), **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.http.post(path, headers=self._h(), json=body if body is not None else {}, **kw)

    def put(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.put(path, headers=self._h(), **kw)

    def delete(self, path: str) -> httpx.Response:
        return self.http.delete(path, headers=self._h())

    # --- catalog -----------------------------------------------------------
    def latest(self, kind: str, name: str) -> dict[str, Any]:
        rows = [a for a in self.get("/v1/catalog/compositions").json() if a["artifact_id"] == name] if kind == "composition" else []
        if not rows:
            raise LookupError(name)
        return rows[-1]

    def publish(self, kind: str, body: dict[str, Any]) -> httpx.Response:
        return self.post(f"/v1/catalog/{kind}/publish", body)

    # --- sessions ----------------------------------------------------------
    def create(self, composition: str, digest: str, harness: str = "githi-v1", **extra: Any) -> httpx.Response:
        body = {"harness_version": harness, "composition_id": composition, "composition_digest": digest} | extra
        return self.post("/v1/sessions", body)

    def say(self, sid: str, text: str, command_id: str | None = None) -> httpx.Response:
        return self.post(f"/v1/sessions/{sid}/messages", {"text": text, "command_id": command_id or uuid.uuid4().hex})

    def events(
        self, sid: str, since: int = -1, quiet_s: float = 1.0, *, query: bool = False
    ) -> list[dict[str, Any]]:
        """The journal so far: SSE replay, read until the stream goes quiet (it always follows).

        The cursor goes in Last-Event-ID, or in ``?since=`` when ``query`` is set.
        """
        out: list[dict[str, Any]] = []
        timeout = httpx.Timeout(connect=10, read=quiet_s, write=10, pool=10)
        cursor = since >= 0
        try:
            with self.http.stream(
                "GET", f"/v1/sessions/{sid}/events", timeout=timeout,
                params={"since": since} if cursor and query else None,
                headers=self._h() | ({"Last-Event-ID": str(since)} if cursor and not query else {}),
            ) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"events {r.status_code}: {r.read()[:200]!r}")
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        out.append(json.loads(line[5:]))
        except httpx.ReadTimeout:
            pass
        return out

    def wait(self, sid: str, done: Callable[[list[dict[str, Any]]], bool], timeout: float = 60) -> list[dict[str, Any]]:
        end = time.time() + timeout
        evs: list[dict[str, Any]] = []
        while time.time() < end:
            evs = self.events(sid)
            if done(evs):
                return evs
            time.sleep(0.5)
        raise TimeoutError(f"condition not met; last events: {types(evs)[-15:]}")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def types(evs: list[dict[str, Any]]) -> list[str]:
    return [e.get("type", "?") for e in evs]


def runs_ended(n: int) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda evs: types(evs).count("run_end") >= n


def payloads(evs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Payloads of ``kind`` (``ToolEnd`` or ``tool_end``)."""
    want = _snake(kind)
    return [e.get("data", {}) for e in evs if e.get("type") == want]


def relay_mark() -> int:
    try:
        with open(RELAY_LOG, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except FileNotFoundError:
        return 0


def relay_since(mark: int) -> list[dict[str, Any]]:
    """Upstream requests the gateway made since ``mark`` (what the model actually received)."""
    try:
        with open(RELAY_LOG, encoding="utf-8") as fh:
            return [json.loads(line) for i, line in enumerate(fh) if i >= mark]
    except FileNotFoundError:
        return []
