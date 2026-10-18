"""Harness RPC command builders. Always send your own id for correlation."""

from __future__ import annotations

from typing import Any


def prompt(message: str, *, id: str) -> dict[str, Any]:
    return {"id": id, "type": "prompt", "message": message}


def abort(*, id: str) -> dict[str, Any]:
    return {"id": id, "type": "abort"}


def get_state(*, id: str) -> dict[str, Any]:
    return {"id": id, "type": "get_state"}


def set_auto_retry(enabled: bool, *, id: str) -> dict[str, Any]:
    return {"id": id, "type": "set_auto_retry", "enabled": enabled}


def extension_ui_response(
    *,
    id: str,
    confirmed: bool | None = None,
    value: str | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    """Reply to a harness extension UI dialog."""
    out: dict[str, Any] = {"type": "extension_ui_response", "id": id}
    if cancelled:
        out["cancelled"] = True
    elif confirmed is not None:
        out["confirmed"] = confirmed
    elif value is not None:
        out["value"] = value
    return out
