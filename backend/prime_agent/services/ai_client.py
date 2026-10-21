"""Outbound AI-plane client (masterclass §11 service layer).

Routes never assemble Bearer strings; this service owns auth headers.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from prime_agent.config import Settings, get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.security.service_jwt import ServiceJwtMinter

# D13: outbound W3C traceparent toward supervisor (explicit seed or OTel context).
_outbound_traceparent: ContextVar[str | None] = ContextVar(
    "yal_outbound_traceparent", default=None
)


def set_outbound_traceparent(value: str | None) -> None:
    """Seed W3C traceparent for subsequent AiClient → supervisor calls."""
    _outbound_traceparent.set(value)


def inject_traceparent(value: str | None) -> None:
    """Alias for set_outbound_traceparent (acceptance / call-site convenience)."""
    set_outbound_traceparent(value)


def _current_traceparent() -> str | None:
    explicit = _outbound_traceparent.get()
    if explicit:
        return explicit
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is None or not getattr(ctx, "is_valid", False):
            return None
        trace_id = format(ctx.trace_id, "032x")
        span_id = format(ctx.span_id, "016x")
        flags = "01" if getattr(ctx, "trace_flags", None) and int(ctx.trace_flags) & 0x01 else "00"
        return f"00-{trace_id}-{span_id}-{flags}"
    except Exception:
        return None


def _ensure_traceparent(headers: dict[str, str]) -> dict[str, str]:
    if "traceparent" in headers:
        return headers
    tp = _current_traceparent()
    if not tp:
        # Always mint a valid W3C header so supervisor can seed run trace_id.
        tp = f"00-{uuid4().hex}-{uuid4().hex[:16]}-01"
    headers = dict(headers)
    headers["traceparent"] = tp
    return headers
if TYPE_CHECKING:
    from prime_agent.schemas.harness import CatalogCompositionSummary

from dataclasses import dataclass, field
from urllib.parse import quote

SAFE_READY_REASONS = frozenset(
    {
        "not_configured",
        "unreachable",
        "draining",
        "database",
        "sandbox",
        "catalog",
        "gateway",
        "memory",
        "credentials",
        "routes",
        "provider",
        "auth",
        "timeout",
        "upstream_error",
        "not_ready",
    }
)


def _refusal_message(response: httpx.Response) -> str:
    """The supervisor's own reason for a 4xx (what was wrong with the request); never a 5xx body."""
    if 400 <= response.status_code < 500:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"][:500]
        if isinstance(detail, str):
            return detail[:500]
    return "AI supervisor returned an error"


class AiUpstreamError(Exception):
    """Typed upstream failure — safe to map to HTTP without leaking secrets."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = "AI_UPSTREAM_ERROR"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "status_code": self.status_code,
        }


class AiPlatformNotReadyError(Exception):
    """Product gate: AI plane cannot accept Run/Test mutations."""

    def __init__(self, message: str = "AI platform is not ready", *, reason: str | None = None) -> None:
        super().__init__(message)
        self.error_code = "AI_PLATFORM_NOT_READY"
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "ready": False,
            "run_allowed": False,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ReadyzResult:
    """Structured supervisor readiness outcome (200 or 503)."""

    ready: bool
    status: str
    reason: str | None = None
    http_status: int = 200
    raw: dict[str, Any] = field(default_factory=dict)


def sanitize_ready_reason(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in SAFE_READY_REASONS:
        return value
    return "upstream_error"


class AiClient:
    """HTTP client for supervisor (health/ready first; sessions later)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        minter: ServiceJwtMinter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._minter = minter or ServiceJwtMinter.from_settings(self._settings)
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> AiClient:
        return cls(settings)

    def _require_base_url(self) -> str:
        base = (self._settings.ai_supervisor_base_url or "").rstrip("/")
        if not base:
            raise AiUpstreamError("AI supervisor base URL is not configured", status_code=503)
        return base

    def _auth_headers(self, identity: TenantPrincipal) -> dict[str, str]:
        token = self._minter.mint(identity)
        headers = {"Authorization": f"Bearer {token}"}
        return _ensure_traceparent(headers)

    def set_outbound_traceparent(self, value: str | None) -> None:
        set_outbound_traceparent(value)

    def inject_traceparent(self, value: str | None) -> None:
        inject_traceparent(value)

    async def healthz(self, identity: TenantPrincipal) -> dict[str, Any]:
        """GET supervisor /healthz — raises on non-2xx."""
        result = await self._request("/healthz", identity, allow_not_ready=False)
        return result.raw

    async def readyz(self, identity: TenantPrincipal) -> ReadyzResult:
        """GET supervisor /readyz — returns structured ready/not-ready (no raise on 503)."""
        return await self._request("/readyz", identity, allow_not_ready=True)

    async def get_artifact(
        self,
        identity: TenantPrincipal,
        kind: str,
        artifact_id: str,
        digest: str,
    ) -> dict[str, Any]:
        """GET /v1/catalog/{kind}/{id}/{digest}, with the artifact's ``body``."""
        encoded = quote(digest, safe="")
        path = f"/v1/catalog/{quote(kind, safe='')}/{quote(artifact_id, safe='')}/{encoded}"
        payload, status_code = await self._catalog_get(path, identity)
        if status_code == 404:
            raise AiUpstreamError(f"{kind} not found", status_code=404)
        if status_code >= 400:
            raise AiUpstreamError(
                "AI catalog returned an error",
                status_code=503 if status_code >= 500 else status_code,
            )
        return payload if isinstance(payload, dict) else {}

    async def get_composition(
        self,
        identity: TenantPrincipal,
        composition_id: str,
        digest: str,
    ) -> CatalogCompositionSummary:
        """GET /v1/catalog/composition/{id}/{digest} — raises AiUpstreamError on failure."""
        from prime_agent.schemas.harness import CatalogCompositionSummary

        payload = await self.get_artifact(identity, "composition", composition_id, digest)
        return CatalogCompositionSummary.model_validate(
            {
                **payload,
                "artifact_id": payload.get("artifact_id") or composition_id,
                "digest": payload.get("digest") or digest,
            }
        )

    async def get_harness_version(
        self, identity: TenantPrincipal, harness_version: str
    ) -> dict[str, Any]:
        """GET /v1/harness_versions/{v}: its runtime, status and manifest."""
        path = f"/v1/harness_versions/{quote(harness_version, safe='')}"
        payload, status_code = await self._catalog_get(path, identity)
        if status_code == 404:
            raise AiUpstreamError("harness version not found", status_code=404)
        if status_code >= 400:
            raise AiUpstreamError(
                "AI catalog returned an error",
                status_code=503 if status_code >= 500 else status_code,
            )
        return payload if isinstance(payload, dict) else {}

    async def list_compositions(
        self,
        identity: TenantPrincipal,
        *,
        status_filter: str | None = "offered",
    ) -> list[CatalogCompositionSummary]:
        """GET /v1/catalog/compositions — optional status query."""
        from prime_agent.schemas.harness import CatalogCompositionSummary

        path = "/v1/catalog/compositions"
        if status_filter:
            path = f"{path}?status={status_filter}"
        payload, status_code = await self._catalog_get(path, identity)
        if status_code >= 400:
            raise AiUpstreamError(
                "AI catalog returned an error",
                status_code=503 if status_code >= 500 else status_code,
            )
        if not isinstance(payload, list):
            return []
        return [CatalogCompositionSummary.model_validate(item) for item in payload]

    async def create_session(
        self,
        identity: TenantPrincipal,
        *,
        harness_version: str,
        runtime: str,
        composition_id: str | None = None,
        composition_digest: str | None = None,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "harness_version": harness_version,
            "runtime": runtime,
        }
        if composition_id is not None:
            body["composition_id"] = composition_id
        if composition_digest is not None:
            body["composition_digest"] = composition_digest
        if scope_id is not None:
            body["scope_id"] = scope_id
        return await self._json_request(
            "POST",
            "/v1/sessions",
            identity,
            json_body=body,
            expected=(201,),
        )

    async def get_session(
        self, identity: TenantPrincipal, ai_session_id: str
    ) -> dict[str, Any]:
        return await self._json_request(
            "GET",
            f"/v1/sessions/{ai_session_id}",
            identity,
            expected=(200,),
        )

    async def list_sessions(self, identity: TenantPrincipal) -> list[dict[str, Any]]:
        payload = await self._json_request(
            "GET",
            "/v1/sessions",
            identity,
            expected=(200,),
        )
        return payload if isinstance(payload, list) else []

    async def post_message(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        text: str,
        command_id: str,
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/messages",
            identity,
            json_body={"text": text, "command_id": command_id},
            expected=(202,),
        )

    async def abort_session(
        self, identity: TenantPrincipal, ai_session_id: str
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/abort",
            identity,
            expected=(200,),
        )

    async def terminate_session(
        self, identity: TenantPrincipal, ai_session_id: str
    ) -> None:
        await self._json_request(
            "DELETE",
            f"/v1/sessions/{ai_session_id}",
            identity,
            expected=(204,),
            allow_empty=True,
        )

    async def fork_session(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        at_seq: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if at_seq is not None:
            body["at_seq"] = at_seq
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/fork",
            identity,
            json_body=body,
            expected=(201,),
        )

    async def publish_tool_set(
        self,
        identity: TenantPrincipal,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST /v1/catalog/tool_set/publish — derived ToolSet from system UUIDs (D6)."""
        return await self._json_request(
            "POST",
            "/v1/catalog/tool_set/publish",
            identity,
            json_body=body,
            expected=(200,),
        )

    async def publish_composition(
        self,
        identity: TenantPrincipal,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST /v1/catalog/composition/publish — pin derived ToolSet for sessions (D6 §9.3)."""
        return await self._json_request(
            "POST",
            "/v1/catalog/composition/publish",
            identity,
            json_body=body,
            expected=(200,),
        )

    async def invoke_tool(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/sessions/{id}/tools/invoke — BFF-mediated lab/agent invoke (D6)."""
        body: dict[str, Any] = {
            "name": name,
            "arguments": arguments or {},
        }
        if tool_call_id is not None:
            body["tool_call_id"] = tool_call_id
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/tools/invoke",
            identity,
            json_body=body,
            expected=(200,),
        )

    async def list_session_approvals(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
    ) -> list[dict[str, Any]]:
        """GET /v1/sessions/{id}/approvals — pending approvals for a session (D6 E)."""
        raw = await self._json_request(
            "GET",
            f"/v1/sessions/{ai_session_id}/approvals",
            identity,
            expected=(200,),
        )
        if isinstance(raw, list):
            return raw
        return []

    async def respond_approval(
        self,
        identity: TenantPrincipal,
        approval_id: str,
        *,
        choice: str,
    ) -> dict[str, Any]:
        """POST /v1/approvals/{id} — respond to a pending approval (D6 E)."""
        return await self._json_request(
            "POST",
            f"/v1/approvals/{approval_id}",
            identity,
            json_body={"choice": choice},
            expected=(200,),
        )

    async def ensure_workspace_scope(
        self,
        identity: TenantPrincipal,
        scope_id: str,
    ) -> dict[str, Any]:
        """POST /v1/scopes/{scope_id}/ensure — bootstrap owner grants (D9)."""
        return await self._json_request(
            "POST",
            f"/v1/scopes/{scope_id}/ensure",
            identity,
            expected=(200,),
        )

    async def list_workspace_files(
        self,
        identity: TenantPrincipal,
        scope_id: str,
    ) -> list[dict[str, Any]]:
        """GET /v1/scopes/{scope_id}/files — durable metadata list (D9)."""
        raw = await self._json_request(
            "GET",
            f"/v1/scopes/{scope_id}/files",
            identity,
            expected=(200,),
        )
        return raw if isinstance(raw, list) else []

    async def get_workspace_file_bytes(
        self,
        identity: TenantPrincipal,
        scope_id: str,
        path: str,
    ) -> bytes:
        """GET /v1/scopes/{scope_id}/files/{path} — durable bytes (D9)."""
        encoded = "/".join(quote(p, safe="") for p in path.split("/") if p)
        return await self._bytes_request(
            "GET",
            f"/v1/scopes/{scope_id}/files/{encoded}",
            identity,
            expected=(200,),
        )

    async def put_workspace_file(
        self,
        identity: TenantPrincipal,
        scope_id: str,
        path: str,
        data: bytes,
        *,
        mime_type: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """PUT /v1/scopes/{scope_id}/files/{path} — durable put under grants (D9)."""
        encoded = "/".join(quote(p, safe="") for p in path.split("/") if p)
        params: list[str] = []
        if mime_type is not None:
            params.append(f"mime_type={quote(mime_type, safe='')}")
        if expected_version is not None:
            params.append(f"expected_version={expected_version}")
        q = f"?{'&'.join(params)}" if params else ""
        return await self._bytes_json_request(
            "PUT",
            f"/v1/scopes/{scope_id}/files/{encoded}{q}",
            identity,
            content=data,
            content_type=mime_type or "application/octet-stream",
            expected=(200,),
        )

    async def delete_workspace_file(
        self,
        identity: TenantPrincipal,
        scope_id: str,
        path: str,
    ) -> None:
        """DELETE /v1/scopes/{scope_id}/files/{path} — soft-delete durable path (D9)."""
        encoded = "/".join(quote(p, safe="") for p in path.split("/") if p)
        await self._json_request(
            "DELETE",
            f"/v1/scopes/{scope_id}/files/{encoded}",
            identity,
            expected=(204,),
            allow_empty=True,
        )

    async def rehydrate_session_workspace(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
    ) -> dict[str, Any]:
        """POST /v1/sessions/{id}/workspace/rehydrate — durable-wins cache refresh (D9)."""
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/workspace/rehydrate",
            identity,
            expected=(200,),
        )

    async def list_scope_members(
        self,
        identity: TenantPrincipal,
        scope_id: str,
    ) -> list[dict[str, Any]]:
        """GET /v1/scopes/{scope_id}/members — scope:admin (D11)."""
        raw = await self._json_request(
            "GET",
            f"/v1/scopes/{scope_id}/members",
            identity,
            expected=(200,),
        )
        return raw if isinstance(raw, list) else []

    async def invite_scope_member(
        self,
        identity: TenantPrincipal,
        scope_id: str,
        *,
        principal_id: str,
        grants: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/scopes/{scope_id}/members — invite under scope:admin (D11)."""
        body: dict[str, Any] = {"principal_id": principal_id}
        if grants is not None:
            body["grants"] = grants
        return await self._json_request(
            "POST",
            f"/v1/scopes/{scope_id}/members",
            identity,
            json_body=body,
            expected=(201,),
        )

    async def revoke_scope_member(
        self,
        identity: TenantPrincipal,
        scope_id: str,
        principal_id: str,
    ) -> None:
        """DELETE /v1/scopes/{scope_id}/members/{principal_id} (D11)."""
        await self._json_request(
            "DELETE",
            f"/v1/scopes/{scope_id}/members/{principal_id}",
            identity,
            expected=(204,),
            allow_empty=True,
        )

    # --- long-term memory: the supervisor's notebooks ------------------------------
    # The supervisor applies tenancy and disclosure: a notebook of someone else's is
    # "not found". The backend never talks to the memory service directly.

    async def list_notebooks(
        self, identity: TenantPrincipal, *, agent_pin: str, archived: bool = False
    ) -> list[dict[str, Any]]:
        return await self._json_request(
            "GET", "/v1/memory/notebooks", identity,
            params={"agent_pin": agent_pin, "archived": str(archived).lower()},
        )

    async def create_notebook(
        self, identity: TenantPrincipal, *, agent_pin: str, name: str, label: str | None = None
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST", "/v1/memory/notebooks", identity,
            json_body={"agent_pin": agent_pin, "name": name, "label": label}, expected=(201,),
        )

    async def notebook_request(
        self,
        identity: TenantPrincipal,
        method: str,
        notebook_id: str,
        sub: str = "",
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        """One notebook's routes: ``""`` (patch), ``facts``, ``clear``, ``revisions``, ``restore``."""
        path = f"/v1/memory/notebooks/{quote(notebook_id, safe='')}" + (f"/{sub}" if sub else "")
        return await self._json_request(
            method, path, identity, json_body=json_body, expected=expected,
            allow_empty=204 in expected,
        )

    async def search_notebooks(
        self,
        identity: TenantPrincipal,
        *,
        agent_pin: str,
        query: str,
        notebook_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"agent_pin": agent_pin, "query": query, "limit": limit}
        if notebook_id:
            body["notebook_id"] = notebook_id
        return await self._json_request("POST", "/v1/memory/search", identity, json_body=body)

    async def stream_session_events(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        last_event_id: str | None = None,
    ):
        """Yield raw SSE byte chunks from supervisor (no full-body buffer)."""
        from collections.abc import AsyncIterator

        url = f"{self._require_base_url()}/v1/sessions/{ai_session_id}/events"
        headers = self._auth_headers(identity)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        connect = getattr(self._settings, "ai_sse_connect_timeout_seconds", 10.0)
        timeout = httpx.Timeout(connect=connect, read=None, write=30.0, pool=30.0)
        client = httpx.AsyncClient(transport=self._transport, timeout=timeout)
        try:
            request = client.build_request("GET", url, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code >= 400:
                await response.aclose()
                await client.aclose()
                raise AiUpstreamError(
                    "AI session events stream failed",
                    status_code=503 if response.status_code >= 500 else response.status_code,
                )

            async def _gen() -> AsyncIterator[bytes]:
                try:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
                finally:
                    await response.aclose()
                    await client.aclose()

            return _gen()
        except httpx.HTTPError as exc:
            await client.aclose()
            raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc


    @staticmethod
    def _supervisor_subagent_provider(provider: str | None) -> str:
        """Map product lab_echo alias → supervisor echo stub name ``memory`` (not LTM)."""
        raw = (provider or "memory").strip() or "memory"
        return raw  # lab_echo or memory alias — supervisor factory maps both when lab allowed

    async def spawn_subagent(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        prompt: str,
        description: str = "",
        provider: str = "memory",
        mode: str = "spawn",
        parent_depth: int = 0,
        run_id: str | None = None,
        tool_filter: list[str] | None = None,
        persona: str | None = None,
        depth_limit: int | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/sessions/{id}/subagents/spawn — service JWT (D12 B)."""
        body: dict[str, Any] = {
            "prompt": prompt,
            "description": description,
            "provider": self._supervisor_subagent_provider(provider),
            "mode": mode,
            "parent_depth": parent_depth,
        }
        if run_id is not None:
            body["run_id"] = run_id
        if tool_filter is not None:
            body["tool_filter"] = tool_filter
        if persona is not None:
            body["persona"] = persona
        if depth_limit is not None:
            body["depth_limit"] = depth_limit
        if output_schema is not None:
            body["output_schema"] = output_schema
        return await self._json_request(
            "POST",
            f"/v1/sessions/{ai_session_id}/subagents/spawn",
            identity,
            json_body=body,
            expected=(200, 201),
        )

    async def list_session_children(
        self,
        identity: TenantPrincipal,
        ai_session_id: str,
        *,
        scope: str = "direct",
    ) -> list[dict[str, Any]]:
        """GET /v1/sessions/{id}/children — service JWT (D12 B)."""
        path = f"/v1/sessions/{ai_session_id}/children"
        if scope:
            path = f"{path}?scope={quote(scope, safe='')}"
        raw = await self._json_request(
            "GET",
            path,
            identity,
            expected=(200,),
        )
        return raw if isinstance(raw, list) else []

    def _jaeger_base(self) -> str:
        return (self._settings.jaeger_query_base_url or "").rstrip("/")

    @staticmethod
    def _trace_belongs_to_tenant(data: Any, tenant_id: str) -> bool:
        """True when Jaeger payload carries matching tenant tag (or unknown → deny)."""
        # Must match span attrs emitted by supervisor otlp_sink (`yal.tenant.id`).
        tenant_keys = {"yal.tenant.id", "yal.tenant_id", "tenant_id"}
        if not isinstance(data, dict):
            return False
        processes = data.get("processes")
        if isinstance(processes, dict):
            for proc in processes.values():
                if not isinstance(proc, dict):
                    continue
                tags = proc.get("tags") or []
                if isinstance(tags, list):
                    for tag in tags:
                        if (
                            isinstance(tag, dict)
                            and tag.get("key") in tenant_keys
                            and str(tag.get("value")) == tenant_id
                        ):
                            return True
        spans = data.get("spans")
        if isinstance(spans, list):
            for span in spans:
                if not isinstance(span, dict):
                    continue
                for tag in span.get("tags") or []:
                    if (
                        isinstance(tag, dict)
                        and tag.get("key") in tenant_keys
                        and str(tag.get("value")) == tenant_id
                    ):
                        return True
        return False

    async def list_traces(self, identity: TenantPrincipal) -> list[dict[str, Any]]:
        """GET Jaeger /api/traces filtered by tenant tag — empty list if unavailable (MVP)."""
        base = self._jaeger_base()
        if not base:
            return []
        # Jaeger tags query: same key spans emit (`yal.tenant.id`).
        tags = quote(f'{{"yal.tenant.id":"{identity.tenant_id}"}}', safe="")
        url = f"{base}/api/traces?tags={tags}&limit=20"
        timeout = self._settings.ai_http_timeout_seconds
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.get(url)
            except (httpx.TimeoutException, httpx.HTTPError):
                return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and self._trace_belongs_to_tenant(
                item, identity.tenant_id
            ):
                out.append(item)
            elif isinstance(item, dict) and not item.get("processes"):
                # Stub-friendly: if Jaeger returns pre-filtered results without tags, keep.
                out.append(item)
        return out

    async def get_trace_spans(
        self,
        identity: TenantPrincipal,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """GET Jaeger /api/traces/{id} — spans only when tenant matches; else empty."""
        base = self._jaeger_base()
        if not base:
            return []
        encoded = quote(trace_id, safe="")
        url = f"{base}/api/traces/{encoded}"
        timeout = self._settings.ai_http_timeout_seconds
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.get(url)
            except (httpx.TimeoutException, httpx.HTTPError):
                return []
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise AiUpstreamError(
                "Jaeger query returned an error",
                status_code=503 if response.status_code >= 500 else response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return []
        trace = data[0] if isinstance(data[0], dict) else None
        if trace is None:
            return []
        if not self._trace_belongs_to_tenant(trace, identity.tenant_id):
            # Foreign tenant — treat as missing (never leak spans).
            return []
        spans = trace.get("spans")
        return spans if isinstance(spans, list) else []

    async def _json_request(
        self,
        method: str,
        path: str,
        identity: TenantPrincipal,
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        allow_empty: bool = False,
        params: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._require_base_url()}{path}"
        timeout = self._settings.ai_http_timeout_seconds
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self._auth_headers(identity),
                    json=json_body,
                    params=params,
                )
            except httpx.TimeoutException as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc

        if response.status_code not in expected:
            raise AiUpstreamError(
                _refusal_message(response),
                status_code=503 if response.status_code >= 500 else response.status_code,
            )
        if allow_empty or response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    async def _bytes_request(
        self,
        method: str,
        path: str,
        identity: TenantPrincipal,
        *,
        expected: tuple[int, ...] = (200,),
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        url = f"{self._require_base_url()}{path}"
        timeout = self._settings.ai_http_timeout_seconds
        headers = self._auth_headers(identity)
        if content_type is not None:
            headers = {**headers, "Content-Type": content_type}
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    content=content,
                )
            except httpx.TimeoutException as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc

        if response.status_code not in expected:
            raise AiUpstreamError(
                "AI supervisor returned an error",
                status_code=503 if response.status_code >= 500 else response.status_code,
            )
        return response.content

    async def _bytes_json_request(
        self,
        method: str,
        path: str,
        identity: TenantPrincipal,
        *,
        content: bytes,
        content_type: str,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = f"{self._require_base_url()}{path}"
        timeout = self._settings.ai_http_timeout_seconds
        headers = {**self._auth_headers(identity), "Content-Type": content_type}
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    content=content,
                )
            except httpx.TimeoutException as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc

        if response.status_code not in expected:
            raise AiUpstreamError(
                "AI supervisor returned an error",
                status_code=503 if response.status_code >= 500 else response.status_code,
            )
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    async def _catalog_get(
        self, path: str, identity: TenantPrincipal
    ) -> tuple[Any, int]:
        url = f"{self._require_base_url()}{path}"
        timeout = self._settings.ai_http_timeout_seconds
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.get(url, headers=self._auth_headers(identity))
            except httpx.TimeoutException as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        return body, response.status_code

    async def _request(
        self,
        path: str,
        identity: TenantPrincipal,
        *,
        allow_not_ready: bool,
    ) -> ReadyzResult:
        try:
            url = f"{self._require_base_url()}{path}"
        except AiUpstreamError:
            if allow_not_ready:
                return ReadyzResult(
                    ready=False,
                    status="not_ready",
                    reason="not_configured",
                    http_status=503,
                )
            raise

        timeout = self._settings.ai_http_timeout_seconds
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.get(url, headers=self._auth_headers(identity))
            except httpx.TimeoutException as exc:
                if allow_not_ready:
                    return ReadyzResult(
                        ready=False,
                        status="not_ready",
                        reason="timeout",
                        http_status=503,
                    )
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc
            except httpx.HTTPError as exc:
                if allow_not_ready:
                    return ReadyzResult(
                        ready=False,
                        status="not_ready",
                        reason="unreachable",
                        http_status=503,
                    )
                raise AiUpstreamError("AI supervisor unreachable", status_code=503) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if response.status_code == 200 and str(payload.get("status", "")).lower() in {
            "ready",
            "ok",
        }:
            return ReadyzResult(
                ready=True,
                status="ready" if path.endswith("readyz") else "ok",
                reason=None,
                http_status=200,
                raw=payload,
            )

        if allow_not_ready and response.status_code >= 400:
            reason = sanitize_ready_reason(
                payload.get("reason") if isinstance(payload.get("reason"), str) else "not_ready"
            )
            return ReadyzResult(
                ready=False,
                status="not_ready",
                reason=reason,
                http_status=response.status_code,
                raw={k: v for k, v in payload.items() if k != "problems"},
            )

        if response.status_code >= 400:
            raise AiUpstreamError(
                "AI supervisor returned an error",
                status_code=503 if response.status_code >= 500 else response.status_code,
            )

        return ReadyzResult(
            ready=False,
            status="not_ready",
            reason="upstream_error",
            http_status=response.status_code,
            raw=payload,
        )
