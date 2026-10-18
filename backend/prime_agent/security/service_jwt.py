"""Service JWT minting for AI plane calls (distinct from user access JWTs).

Claim policy: contracts/service_auth.md
Factory-style helpers follow masterclass §6.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt

from prime_agent.config import Settings, get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal


class ServiceJwtMinter:
    """Mint short-lived service JWTs for supervisor (and later gateway/memory)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @classmethod
    def from_settings(cls, settings: Settings) -> ServiceJwtMinter:
        return cls(settings)

    def mint(self, identity: TenantPrincipal, *, audience: str | None = None) -> str:
        """Encode a service JWT. Raises if the signing secret is not configured.

        ``audience`` defaults to the supervisor audience; pass the Memory
        audience when calling the Durable Memory Service.
        """
        secret = self._settings.ai_service_jwt_secret
        if not secret:
            raise RuntimeError("AI_SERVICE_JWT_SECRET is not configured")

        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": self._settings.ai_service_id,
            "iss": self._settings.ai_service_jwt_issuer,
            "aud": audience or self._settings.ai_service_jwt_audience,
            "type": "service",
            "tenant_id": identity.tenant_id,
            "principal_id": identity.principal_id,
            "iat": now,
            "exp": now + timedelta(seconds=self._settings.ai_service_jwt_ttl_seconds),
            "jti": str(uuid4()),
        }
        return jwt.encode(
            payload,
            secret,
            algorithm=self._settings.algorithm,
        )
