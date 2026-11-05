"""Checking a vendor key before it is saved.

QM refuses a key the vendor does not accept (``validateProviderApiKey``: one authenticated GET,
five seconds, saved only on a success). Here a failure is told apart: the vendor refusing the
key (HTTP 401/403) is the key's fault, anything else (unreachable, a 5xx, a timeout) says
nothing about the key, so nothing is saved and the caller may try again.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import httpx

from supervisor.gateway.providers.anthropic import ANTHROPIC_VERSION
from supervisor.gateway.vendors import Vendor

__all__ = ["KeyCheck", "check_key"]

TIMEOUT_S: Final = 5.0


class KeyCheck(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    UNREACHABLE = "unreachable"
    UNCHECKED = "unchecked"
    """The vendor documents no call to check a key with."""


def _headers(vendor: Vendor, key: str) -> dict[str, str]:
    if vendor.api == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def check_key(
    vendor: Vendor, key: str, base_url: str, *, transport: httpx.BaseTransport | None = None
) -> KeyCheck:
    """Whether ``vendor`` at ``base_url`` accepts ``key``."""
    if vendor.key_check is None or not base_url:
        return KeyCheck.UNCHECKED
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False) as client:
            response = client.get(f"{base_url}{vendor.key_check}", headers=_headers(vendor, key))
    except httpx.HTTPError:
        return KeyCheck.UNREACHABLE
    if response.is_success:
        return KeyCheck.ACCEPTED
    return KeyCheck.REFUSED if response.status_code in (401, 403) else KeyCheck.UNREACHABLE
