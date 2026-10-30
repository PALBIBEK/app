"""Views across the user's sessions, and the platform's operating pages.

- ``GET /approvals``: every approval waiting on the user, across their sessions;
- ``GET /sandboxes``: their sessions' sandboxes and what state each is in;
- ``GET /usage``: tokens and cost, by day, agent or session;
- ``/runners`` and ``/secrets``: the user's runners and tenant secrets;
- ``GET /ops/workers`` and ``GET /ops/rollout``: the worker fleet (owners and admins).

The supervisor names sessions by its own ids; the browser knows this product's
session ids. Every row is translated through the user's session links (with the
agent's name), and a supervisor session the user has no link to is left out.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import get_settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.session_link import SessionLink
from prime_agent.db.models.user import User
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.web.dependencies import CurrentUser, DbSession
from prime_agent.web.dependencies.auth import require_any_role

router = APIRouter()

OpsUser = Annotated[User, Depends(require_any_role("owner", "admin"))]
"""Who sees the worker fleet: owners and admins."""

_RUNNER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_SECRET_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}")


def _client() -> AiClient:
    return AiClient.from_settings(get_settings())


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except AiUpstreamError as exc:
        raise HTTPException(status_code=exc.status_code or 503, detail=exc.to_dict()) from exc


class _Link(BaseModel):
    session_id: str
    title: str | None
    harness_id: str | None
    harness_name: str | None


async def _links(db: AsyncSession, user_id: str) -> dict[str, _Link]:
    """The user's sessions keyed by the supervisor's id, with each agent's name."""
    rows = (
        await db.execute(
            select(SessionLink, Harness.name)
            .outerjoin(Harness, Harness.id == SessionLink.harness_id)
            .where(SessionLink.user_id == user_id)
        )
    ).all()
    return {
        link.ai_session_id: _Link(
            session_id=link.id, title=link.title, harness_id=link.harness_id, harness_name=name
        )
        for link, name in rows
    }


# --- approvals inbox ---------------------------------------------------------------------------


class InboxItem(BaseModel):
    approval_id: str
    session_id: str
    session_title: str | None = None
    harness_id: str | None = None
    harness_name: str | None = None
    prompt: str
    options: list[str]
    created_at_ms: int | None = None
    expires_at_ms: int | None = None
    subagent: bool = False
    """A call of one of the session's subagents waits (answered from the session)."""
    subagent_label: str | None = None


@router.get("/approvals", response_model=list[InboxItem])
async def approval_inbox(db: DbSession, current_user: CurrentUser) -> list[InboxItem]:
    """Every approval waiting for an answer in the user's sessions, oldest first.

    Answer one with ``POST /sessions/{session_id}/approvals/{approval_id}``.
    """
    rows = await _call(_client().approval_inbox(TenantPrincipal.from_user(current_user)))
    links = await _links(db, current_user.id)
    out: list[InboxItem] = []
    for row in rows:
        # A subagent's call is answered from the session at the top of its tree.
        root = row.get("root_session_id")
        link = links.get(str(root or row.get("session_id")))
        if link is None:
            continue
        out.append(
            InboxItem(
                approval_id=str(row["approval_id"]),
                session_id=link.session_id,
                session_title=row.get("session_title") or link.title,
                harness_id=link.harness_id,
                harness_name=link.harness_name or row.get("agent"),
                prompt=str(row.get("prompt") or ""),
                options=[str(o) for o in row.get("options") or []],
                created_at_ms=row.get("created_at_ms"),
                expires_at_ms=row.get("expires_at_ms"),
                subagent=root is not None,
                subagent_label=row.get("subagent_label") or None,
            )
        )
    return out


# --- sandboxes ---------------------------------------------------------------------------------


class SandboxItem(BaseModel):
    session_id: str
    session_title: str | None = None
    harness_name: str | None = None
    hand: str
    state: Literal["running", "stopped", "lost", "not_started"]
    sandbox_id: str | None = None
    provider: str | None = None
    since_ms: int | None = None
    cause: str | None = None
    last_checkpoint_ms: int | None = None
    checkpoint_files: int = 0
    processes: list[str] = Field(default_factory=list)
    reason: str | None = None
    logs_ref: str | None = None
    lease_holder: str | None = None
    lease_expires_ms: int | None = None


@router.get("/sandboxes", response_model=list[SandboxItem])
async def sandboxes(
    db: DbSession,
    current_user: CurrentUser,
    session_id: str | None = Query(default=None, description="One of the user's sessions; else all open ones"),
    include_ended: bool = Query(default=False),
) -> list[SandboxItem]:
    """The sandboxes ("hands") of the user's sessions and what state each is in."""
    links = await _links(db, current_user.id)
    ai_session_id: str | None = None
    if session_id is not None:
        ai_session_id = next((ai for ai, link in links.items() if link.session_id == session_id), None)
        if ai_session_id is None:
            raise HTTPException(status_code=404, detail="session not found")
    rows = await _call(
        _client().list_sandboxes(
            TenantPrincipal.from_user(current_user), ai_session_id=ai_session_id, include_ended=include_ended
        )
    )
    out: list[SandboxItem] = []
    for row in rows:
        link = links.get(str(row.get("session_id")))
        if link is None:
            continue
        item = {k: v for k, v in row.items() if k in SandboxItem.model_fields}
        item.update(session_id=link.session_id, session_title=link.title, harness_name=link.harness_name)
        out.append(SandboxItem(**item))
    return out


# --- usage -------------------------------------------------------------------------------------


class UsageRow(BaseModel):
    key: str
    label: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    model_calls: int
    sessions: int


class UsageOut(BaseModel):
    since_ms: int
    until_ms: int
    by: Literal["agent", "day", "session"]
    rows: list[UsageRow]
    total: UsageRow


@router.get("/usage", response_model=UsageOut)
async def usage(
    db: DbSession,
    current_user: CurrentUser,
    by: Literal["agent", "day", "session"] = Query(default="day"),
    since_ms: int | None = Query(default=None, ge=0),
    until_ms: int | None = Query(default=None, ge=0),
) -> UsageOut:
    """Tokens and cost of every model call in the window (default: the last 30 days)."""
    raw = await _call(
        _client().usage(TenantPrincipal.from_user(current_user), by=by, since_ms=since_ms, until_ms=until_ms)
    )
    links = await _links(db, current_user.id)
    by_agent: dict[str, str] = {}
    if by == "agent":
        # The supervisor groups by composition; name each the way the user named the agent.
        rows = (
            await db.execute(
                select(SessionLink.composition_id, Harness.name)
                .join(Harness, Harness.id == SessionLink.harness_id)
                .where(SessionLink.user_id == current_user.id, SessionLink.composition_id.is_not(None))
            )
        ).all()
        by_agent = {str(c): str(n) for c, n in rows}

    def label(key: str) -> str:
        if by == "agent":
            # A composition this product named (h-…) that no agent of the user's has any more.
            return by_agent.get(key) or (f"{key} (removed)" if key.startswith("h-") else key)
        if by == "session":
            link = links.get(key)
            if link is None:
                return key
            return link.title or (f"{link.harness_name} session" if link.harness_name else link.session_id[:13])
        return key

    def row(r: dict[str, Any], key: str | None = None) -> UsageRow:
        k = key if key is not None else str(r.get("key", ""))
        k_out = links[k].session_id if by == "session" and k in links else k
        return UsageRow(
            key=k_out,
            label=label(k) if key is None else "Total",
            input_tokens=int(r.get("input_tokens", 0)),
            output_tokens=int(r.get("output_tokens", 0)),
            total_tokens=int(r.get("total_tokens", 0)),
            cost=float(r.get("cost", 0.0)),
            model_calls=int(r.get("model_calls", 0)),
            sessions=int(r.get("sessions", 0)),
        )

    rows_in = raw.get("rows") or []
    if by == "session":
        rows_in = [r for r in rows_in if str(r.get("key")) in links]
    return UsageOut(
        since_ms=int(raw.get("since_ms", 0)),
        until_ms=int(raw.get("until_ms", 0)),
        by=by,
        rows=[row(r) for r in rows_in],
        total=row(raw.get("total") or {}, key="total"),
    )


# --- runners -----------------------------------------------------------------------------------


class RunnerOut(BaseModel):
    name: str
    online: bool


class RunnerToken(BaseModel):
    name: str
    token: str


def _runner_name(name: str) -> str:
    if not _RUNNER_NAME.fullmatch(name):
        raise HTTPException(status_code=422, detail="runner names: lowercase letters, digits, '-', '_'")
    return name


@router.get("/runners", response_model=list[RunnerOut])
async def list_runners(current_user: CurrentUser) -> list[RunnerOut]:
    rows = await _call(_client().list_runners(TenantPrincipal.from_user(current_user)))
    return [RunnerOut(name=str(r.get("name")), online=bool(r.get("online"))) for r in rows]


@router.post("/runners/{name}", response_model=RunnerToken)
async def register_runner(name: str, current_user: CurrentUser) -> RunnerToken:
    """Register (or re-key) a runner. The token is shown this once; re-keying stops the old one."""
    reply = await _call(_client().register_runner(TenantPrincipal.from_user(current_user), _runner_name(name)))
    return RunnerToken(name=name, token=str(reply.get("token", "")))


@router.delete("/runners/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_runner(name: str, current_user: CurrentUser) -> Response:
    await _call(_client().remove_runner(TenantPrincipal.from_user(current_user), _runner_name(name)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- secrets -----------------------------------------------------------------------------------


class SecretNames(BaseModel):
    names: list[str]


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=65_536)


def _secret_name(name: str) -> str:
    if not _SECRET_NAME.fullmatch(name) or ".." in name:
        raise HTTPException(status_code=422, detail="secret names: letters, digits, '_', '.', '-', '/'")
    return name


@router.get("/secrets", response_model=SecretNames)
async def list_secrets(current_user: CurrentUser) -> SecretNames:
    """Secret names only: a value is written, never read back."""
    return SecretNames(names=await _call(_client().list_secrets(TenantPrincipal.from_user(current_user))))


@router.put("/secrets/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def put_secret(name: str, body: SecretIn, current_user: CurrentUser) -> Response:
    await _call(_client().put_secret(TenantPrincipal.from_user(current_user), _secret_name(name), body.value))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/secrets/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(name: str, current_user: CurrentUser) -> Response:
    await _call(_client().delete_secret(TenantPrincipal.from_user(current_user), _secret_name(name)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the worker fleet (admins) -----------------------------------------------------------------


@router.get("/ops/workers")
async def ops_workers(current_user: OpsUser) -> dict[str, Any]:
    """Who serves the session queue, how busy each worker is, what waits."""
    result: dict[str, Any] = await _call(_client().worker_fleet(TenantPrincipal.from_user(current_user)))
    return result


@router.get("/ops/rollout")
async def ops_rollout(current_user: OpsUser) -> dict[str, Any]:
    """Which worker versions serve, and how far a deploy has got."""
    result: dict[str, Any] = await _call(_client().rollout_status(TenantPrincipal.from_user(current_user)))
    return result
