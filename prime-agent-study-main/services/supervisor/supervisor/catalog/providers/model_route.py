"""Runtime model_route selection (D5 E1) — catalog pin stays digest-stable."""

from __future__ import annotations

import os


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def apply_runtime_model_route(pinned: str | None) -> str | None:
    """Override composition ``model_route`` without mutating catalog digests.

    Keys stay in the **gateway** process. Supervisor only sees signals:
    ``YAL_MODEL_ROUTE``, ``GATEWAY_VENDOR_READY``, or ``MODEL_GATEWAY_DEFAULT_ROUTE``
    (mirrored from compose — not vendor API keys).

    - ``YAL_MODEL_ROUTE`` wins when set (ops/local demo).
    - Non-echo pins are kept.
    - Echo upgrades when vendor-ready signal or non-echo default route is set.
    """
    explicit = os.environ.get("YAL_MODEL_ROUTE", "").strip()
    if explicit:
        return explicit
    if pinned and pinned != "echo":
        return pinned
    default_route = os.environ.get("MODEL_GATEWAY_DEFAULT_ROUTE", "").strip()
    if default_route and default_route != "echo":
        return default_route
    if _truthy("GATEWAY_VENDOR_READY"):
        return "githi"
    # Legacy local signal: only if ops mistakenly mirrored keys onto supervisor.
    # Prefer GATEWAY_VENDOR_READY / YAL_MODEL_ROUTE (no key custody on supervisor).
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return "githi"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return pinned
