"""One-time codes from a saved authenticator key (RFC 6238 TOTP), for ``browser_vault``'s
``enter_code``: Hermes's ``normalize_otp_secret`` and ``totp_now`` (``agent/vault_store.py``).

A login saved with its authenticator key (the base32 secret, or the ``otpauth://totp/...``
link a site's QR code holds) gets its second factor typed for the agent: the code is made
here, in the supervisor, when the page asks for it, and never reaches the model. Standard
library only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time
from typing import Final
from urllib.parse import parse_qs, urlparse

__all__ = ["InvalidOtpSecret", "normalize_otp_secret", "totp_now"]

_ALGORITHMS: Final = {"SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}


class InvalidOtpSecret(ValueError):
    """Not an authenticator key this can make codes from (the message holds no secret)."""


def normalize_otp_secret(value: str) -> str:
    """A raw base32 seed or an ``otpauth://totp/...`` link, as stored: the bare upper-case
    seed, followed by ``|digits|period|algorithm`` ONLY when the link departs from RFC 6238's
    defaults (6 / 30 / SHA1), so a plain seed stays a plain seed. Other parameters are kept,
    not dropped: an 8-digit or 60-second authenticator would otherwise get wrong codes."""
    value = (value or "").strip()
    if not value:
        return ""
    digits, period, algorithm = 6, 30, "SHA1"
    if value.lower().startswith("otpauth://"):
        parsed = urlparse(value)
        if parsed.netloc.lower() != "totp":
            raise InvalidOtpSecret(
                "only otpauth://totp links are supported (counter-based HOTP is not)"
            )
        query = {k.lower(): v[0] for k, v in parse_qs(parsed.query).items()}
        value = query.get("secret", "")
        try:
            digits = int(query.get("digits", digits))
            period = int(query.get("period", period))
        except ValueError:
            raise InvalidOtpSecret(
                "an otpauth:// link's digits and period must be integers"
            ) from None
        algorithm = query.get("algorithm", algorithm).upper().replace("-", "")
        if digits not in (6, 7, 8) or period <= 0 or algorithm not in _ALGORITHMS:
            raise InvalidOtpSecret(
                "unsupported otpauth:// parameters (digits 6-8, period > 0, SHA1/SHA256/SHA512)"
            )
    seed = re.sub(r"[\s-]", "", value).upper().rstrip("=")
    if not seed or re.search(r"[^A-Z2-7]", seed):
        raise InvalidOtpSecret("an authenticator key is a base32 secret or an otpauth://totp link")
    if (digits, period, algorithm) == (6, 30, "SHA1"):
        return seed
    return f"{seed}|{digits}|{period}|{algorithm}"


def totp_now(seed: str, *, digits: int = 6, period: int = 30, at: float | None = None) -> str:
    """The RFC 6238 code for a stored seed (``normalize_otp_secret``'s form) at ``at`` (now)."""
    algorithm = "sha1"
    if "|" in seed:
        seed, d, p, a = seed.split("|", 3)
        digits, period, algorithm = int(d), int(p), _ALGORITHMS.get(a.upper(), "sha1")
    key = base64.b32decode(seed + "=" * (-len(seed) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, algorithm)).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)
