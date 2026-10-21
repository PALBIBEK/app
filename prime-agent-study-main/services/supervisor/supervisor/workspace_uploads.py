"""Direct uploads into a workspace: the client sends bytes to object storage itself.

The flow (the usual production one, as S3-backed apps and OpenAI's Uploads API do it):

1. **Start**: the client says what it will send (path, size, SHA-256, type). The API checks
   the grant, the path, the size limit and the expected version, then returns a short-lived
   signed URL for a *staging* key of its own (``uploads/<scope>/<id>``) and a signed ticket.
   Bytes this workspace already stores (same SHA-256, any path or version) are recorded at
   once with nothing to send. Only this workspace is looked in, so the answer reveals nothing
   about files in any other.
2. **Upload**: the client PUTs the bytes to that URL. The URL signs the length, type and
   checksum, so S3 refuses anything else; stores that do not check (MinIO, SeaweedFS, moto)
   are caught in step 3.
3. **Complete**: the client sends the ticket back. The API measures what the staging object
   really holds (S3's own checksum, else a streamed SHA-256), refuses and deletes it if it
   is not what was announced, then copies it inside the store to the new version's own
   private, opaque key (``workspaces/<scope>/objects/<id>``) and records the version. A client
   only ever writes its staging key, never a recorded version's bytes.

The ticket is an HMAC-signed, expiring statement of what was announced, so the complete
step needs no database row and cannot be pointed at another scope, path or content.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_URL_TTL_S = 900


def max_upload_bytes() -> int:
    """Largest file a workspace accepts (``WORKSPACE_MAX_UPLOAD_BYTES``, default 100 MiB)."""
    raw = os.environ.get("WORKSPACE_MAX_UPLOAD_BYTES", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_UPLOAD_BYTES


def url_ttl_s() -> int:
    raw = os.environ.get("WORKSPACE_UPLOAD_URL_TTL_S", "").strip()
    return int(raw) if raw.isdigit() and 60 <= int(raw) <= 3600 else DEFAULT_URL_TTL_S


def staging_key(scope_id: str, upload_id: str) -> str:
    return f"uploads/{scope_id}/{upload_id}"


class TicketInvalid(ValueError):
    """The ticket is forged, for another scope, or expired."""


@dataclass(frozen=True)
class UploadTicket:
    upload_id: str
    scope_id: str
    path: str
    content_sha256: str
    size_bytes: int
    mime_type: str
    expected_version: int | None
    expires_at: int

    @property
    def staging_key(self) -> str:
        return staging_key(self.scope_id, self.upload_id)


_dev_secret = secrets.token_bytes(32)


def _secret() -> bytes:
    for name in ("WORKSPACE_UPLOAD_SECRET", "CAPABILITY_SECRET", "SERVICE_JWT_SECRET"):
        if value := os.environ.get(name, "").strip():
            return value.encode()
    # One process only (tests, a bare dev run): API replicas must share a configured secret.
    return _dev_secret


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_ticket(
    *,
    scope_id: str,
    path: str,
    content_sha256: str,
    size_bytes: int,
    mime_type: str,
    expected_version: int | None,
    ttl_s: int,
) -> UploadTicket:
    return UploadTicket(
        upload_id=secrets.token_hex(16),
        scope_id=scope_id,
        path=path,
        content_sha256=content_sha256,
        size_bytes=size_bytes,
        mime_type=mime_type,
        expected_version=expected_version,
        # The ticket outlives the URL a little, so an upload that ends late can still complete.
        expires_at=int(time.time()) + ttl_s + 300,
    )


def sign(ticket: UploadTicket) -> str:
    body = _b64(json.dumps(asdict(ticket), separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify(token: str, *, scope_id: str, now: float | None = None) -> UploadTicket:
    body, _, mac = token.partition(".")
    if not body or not mac:
        raise TicketInvalid("malformed upload ticket")
    want = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    try:
        ok = hmac.compare_digest(want, _unb64(mac))
        fields = json.loads(_unb64(body)) if ok else None
    except (ValueError, json.JSONDecodeError) as exc:
        raise TicketInvalid("malformed upload ticket") from exc
    if not ok or not isinstance(fields, dict):
        raise TicketInvalid("upload ticket signature does not match")
    try:
        ticket = UploadTicket(**fields)
    except TypeError as exc:
        raise TicketInvalid("malformed upload ticket") from exc
    if ticket.scope_id != scope_id:
        raise TicketInvalid("upload ticket is for another workspace")
    if ticket.expires_at < (now if now is not None else time.time()):
        raise TicketInvalid("upload ticket expired; start the upload again")
    return ticket
