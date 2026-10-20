"""Mint/revoke gateway virtual keys via service token (WP-M5)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from supervisor.seams.services.virtual_keys import MintedVirtualKey, VirtualKeyClient


class HttpVirtualKeyClient:
    """HTTP client for model-gateway ``/v1/virtual-keys`` (service auth)."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout: float = 15.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url required")
        if not service_token:
            raise ValueError("service_token required to mint virtual keys")
        self._base = base_url.rstrip("/")
        self._token = service_token
        self._client = httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    def mint(
        self,
        *,
        tenant_id: str,
        session_id: str,
        principal_id: str,
        allowed_models: list[str] | None = None,
        ttl_s: int = 3600,
        max_budget_usd: float = 5.0,
        scope_id: str | None = None,
    ) -> MintedVirtualKey:
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "principal_id": principal_id,
            "allowed_models": allowed_models or [],
            "ttl_s": ttl_s,
            "max_budget_usd": max_budget_usd,
            "scope_id": scope_id,
        }
        resp = self._client.post(
            f"{self._base}/v1/virtual-keys",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"virtual key mint failed {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        return MintedVirtualKey(
            key_id=str(data["key_id"]),
            token=str(data["token"]),
            expires_at=float(data["expires_at"]),
            session_id=str(data["session_id"]),
            tenant_id=str(data["tenant_id"]),
        )

    def revoke_session(self, session_id: str) -> int:
        resp = self._client.post(
            f"{self._base}/v1/virtual-keys/revoke",
            params={"session_id": session_id},
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"virtual key revoke failed {resp.status_code}: {resp.text}"
            )
        return int(resp.json().get("revoked", 0))

    def close(self) -> None:
        self._client.close()


def maybe_virtual_key_client() -> VirtualKeyClient | None:
    """Build client when GATEWAY_BASE_URL + service token are configured."""
    url = (os.environ.get("GATEWAY_BASE_URL") or "").strip()
    token = (
        os.environ.get("GATEWAY_TOKEN")
        or os.environ.get("MODEL_GATEWAY_TOKEN")
        or ""
    ).strip()
    if not url or not token:
        return None
    return HttpVirtualKeyClient(base_url=url, service_token=token)
