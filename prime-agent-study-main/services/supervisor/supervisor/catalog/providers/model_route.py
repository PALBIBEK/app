"""Runtime model_route selection (D5 E1) — catalog pin stays digest-stable."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def apply_runtime_model_route(pinned: str | None) -> str | None:
    """The route a session actually uses: the composition's pin, unless ops chose otherwise.

    Only explicit operator signals change it, and a change is logged, because a pin
    that silently resolves to another model breaks the promise that a composition
    decides its model:

    - ``YAL_MODEL_ROUTE`` wins when set (ops / local demo);
    - a pin other than ``echo`` is kept;
    - an ``echo`` pin (the shipped placeholder) is upgraded by ``MODEL_GATEWAY_DEFAULT_ROUTE``
      (when not ``echo``) or ``GATEWAY_VENDOR_READY`` (to ``githi``).

    A provider key in this process's environment is not a signal: keys belong to the
    gateway, and sniffing them here sent compositions pinned to ``echo`` to a paid
    model nobody chose.
    """
    effective = _effective(pinned)
    if effective != pinned:
        logger.warning("model route %r overridden to %r by deployment settings", pinned, effective)
    return effective


def _effective(pinned: str | None) -> str | None:
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
    return pinned
