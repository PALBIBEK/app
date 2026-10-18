"""Service JWT verification for inbound supervisor calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt.exceptions import PyJWTError


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    tenant_id: str
    principal_id: str
    service_id: str


class ServiceJwtVerifier:
    """Verify service JWTs per contracts/service_auth.md."""

    def __init__(
        self,
        *,
        secret: str,
        audience: str = "yal-supervisor",
        issuer: str = "prime-agent-api",
        algorithm: str = "HS256",
    ) -> None:
        self._secret = secret
        self._audience = audience
        self._issuer = issuer
        self._algorithm = algorithm

    def verify(self, token: str) -> ServiceIdentity:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=0,
                options={
                    "require": [
                        "sub",
                        "exp",
                        "iat",
                        "jti",
                        "iss",
                        "aud",
                        "type",
                        "tenant_id",
                        "principal_id",
                    ],
                },
            )
        except PyJWTError as exc:
            raise ValueError("invalid service token") from exc

        if payload.get("type") != "service":
            raise ValueError("invalid service token")

        tenant_id = str(payload.get("tenant_id") or "").strip()
        principal_id = str(payload.get("principal_id") or "").strip()
        service_id = str(payload.get("sub") or "").strip()
        if not tenant_id or not principal_id or not service_id:
            raise ValueError("invalid service token")

        return ServiceIdentity(
            tenant_id=tenant_id,
            principal_id=principal_id,
            service_id=service_id,
        )
