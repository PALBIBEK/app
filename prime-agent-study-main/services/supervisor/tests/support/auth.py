"""Service JWTs for API tests, minted when a request is sent.

A token made at import time expires (five minutes) while a long suite is still
running earlier modules, and every request then gets 401. ``ServiceAuth`` is a
headers mapping that signs a fresh token each time a client reads it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"
LIFETIME = timedelta(minutes=5)


def service_token(tenant_id: str, principal_id: str | None = None) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "prime-agent-bff",
        "iss": "prime-agent-api",
        "aud": "yal-supervisor",
        "type": "service",
        "tenant_id": tenant_id,
        "principal_id": principal_id or tenant_id,
        "iat": now,
        "exp": now + LIFETIME,
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


class ServiceAuth(Mapping[str, str]):
    """``{"Authorization": "Bearer <fresh token>"}`` for one tenant and principal."""

    def __init__(self, tenant_id: str, principal_id: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.principal_id = principal_id or tenant_id

    def __getitem__(self, key: str) -> str:
        if key != "Authorization":
            raise KeyError(key)
        return f"Bearer {service_token(self.tenant_id, self.principal_id)}"

    def __iter__(self) -> Iterator[str]:
        return iter(("Authorization",))

    def __len__(self) -> int:
        return 1
