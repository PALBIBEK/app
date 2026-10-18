"""Thin HTTP helpers for Durable Memory Service (Tier 2 items / recall).

Auth + scope resolution shared by ContextMemory durable recall and the
``remember`` catalog tool. Service URL/token from env (same as bind materialization).
"""

from __future__ import annotations

import os
from typing import Any


def memory_service_base_url() -> str:
    return os.environ.get("MEMORY_SERVICE_URL", "").strip().rstrip("/")


def memory_service_credential(tenant_id: str) -> str | None:
    """Mint a short-lived service JWT with tenant claim (preferred), else static token.

    JWT-first when ``SERVICE_JWT_SECRET`` / ``MEMORY_SERVICE_JWT_SECRET`` is set so
    Memory binds a verified ``tenant_id`` claim. Static ``MEMORY_SERVICE_TOKEN`` is
    residual (CI / local with ``MEMORY_ALLOW_STATIC_TOKEN=1`` on the Memory side).
    """
    secret = (
        os.environ.get("MEMORY_SERVICE_JWT_SECRET", "").strip()
        or os.environ.get("SERVICE_JWT_SECRET", "").strip()
    )
    if secret and tenant_id.strip():
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        import jwt

        now = datetime.now(UTC)
        aud = (
            os.environ.get("MEMORY_JWT_AUDIENCE", "yal-memory").split(",")[0].strip()
            or "yal-memory"
        )
        payload = {
            "sub": os.environ.get("MEMORY_JWT_SUB", "yal-supervisor").strip()
            or "yal-supervisor",
            "iss": os.environ.get("MEMORY_JWT_ISSUER", "prime-agent-api").strip()
            or "prime-agent-api",
            "aud": aud,
            "type": "service",
            "tenant_id": tenant_id.strip(),
            "principal_id": f"session:{tenant_id.strip()}",
            "iat": now,
            "exp": now + timedelta(hours=12),
            "jti": str(uuid4()),
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    static = os.environ.get("MEMORY_SERVICE_TOKEN", "").strip()
    return static or None

def resolve_memory_scope(
    *,
    session_id: str,
    tenant_id: str = "",
    subject: str | None = None,
    namespace: str | None = None,
) -> dict[str, str]:
    """Build Scope{tenant,subject,namespace} using session extras / env defaults."""
    sid = (session_id or "").strip() or "unknown"
    tenant = (
        (tenant_id or "").strip()
        or os.environ.get("MEMORY_TENANT", "").strip()
        or "default"
    )
    sub = (
        (subject or "").strip()
        or os.environ.get("MEMORY_SUBJECT", "").strip()
        or f"session:{sid}"
    )
    ns = (
        (namespace or "").strip()
        or os.environ.get("MEMORY_NAMESPACE", "").strip()
        or "facts"
    )
    return {"tenant": tenant, "subject": sub, "namespace": ns}


def _auth_headers(tenant_id: str, token: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    cred = token if token is not None else memory_service_credential(tenant_id)
    if cred:
        headers["Authorization"] = f"Bearer {cred}"
    if tenant_id.strip():
        headers["X-Tenant-Id"] = tenant_id.strip()
    return headers


def write_memory_item(
    *,
    content: str,
    scope: dict[str, str],
    source_session: str | None = None,
    confidence: float = 0.8,
    base_url: str | None = None,
    service_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST ``/v1/memory/items`` → ``{"id": ...}``."""
    import httpx

    base = (base_url if base_url is not None else memory_service_base_url()).rstrip("/")
    if not base:
        raise RuntimeError("MEMORY_SERVICE_URL not configured")
    tenant = scope["tenant"]
    body: dict[str, Any] = {
        "scope": scope,
        "content": content,
        "confidence": confidence,
    }
    if source_session:
        body["source_session"] = source_session
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            f"{base}/v1/memory/items",
            json=body,
            headers=_auth_headers(tenant, service_token),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"memory write failed ({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()


def recall_memory_items(
    *,
    query: str,
    scope: dict[str, str],
    max_tokens: int = 512,
    log_session_id: str | None = None,
    base_url: str | None = None,
    service_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST ``/v1/memory/recall`` → pack with ``items`` / ``item_ids`` / ``tokens_used``."""
    import httpx

    base = (base_url if base_url is not None else memory_service_base_url()).rstrip("/")
    if not base:
        raise RuntimeError("MEMORY_SERVICE_URL not configured")
    tenant = scope["tenant"]
    body: dict[str, Any] = {
        "scope": scope,
        "query": query,
        "max_tokens": max_tokens,
    }
    if log_session_id:
        body["log_session_id"] = log_session_id
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            f"{base}/v1/memory/recall",
            json=body,
            headers=_auth_headers(tenant, service_token),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"memory recall failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            return {"item_ids": [], "tokens_used": 0, "items": []}
        return data
