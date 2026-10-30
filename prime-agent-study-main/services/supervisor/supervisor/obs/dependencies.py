"""Whether each service the supervisor depends on answers now (``GET /v1/ops/metrics``).

Probed together, each within ``PROBE_TIMEOUT_S``: the database, Temporal, the sandbox
provider, the model gateway (``MODEL_GATEWAY_URL``) and the memory service
(``MEMORY_SERVICE_URL``), each only where this deployment uses it. A probe that fails,
raises or takes too long says the dependency is down; it never raises itself.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Final

import httpx

__all__ = ["PROBE_TIMEOUT_S", "dependencies_up", "prometheus"]

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S: Final = 3.0

Probe = Callable[[], Awaitable[bool]]


async def dependencies_up(state: Any, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, bool]:
    """Each dependency this deployment uses, and whether it answered (``transport``: for
    the HTTP services' probes, in tests)."""
    probes: dict[str, Probe] = {}
    if (check_db := getattr(state, "check_db", None)) is not None:
        probes["database"] = check_db
    if (workflows := getattr(state, "workflows", None)) is not None:
        probes["temporal"] = workflows.check_reachable
    if (check_sandbox := getattr(state, "check_sandbox", None)) is not None:
        probes["sandbox"] = check_sandbox
    for name, env in (("model_gateway", "MODEL_GATEWAY_URL"), ("memory", "MEMORY_SERVICE_URL")):
        if url := os.environ.get(env, "").strip():
            probes[name] = partial(_ready, url, transport)
    answers = await asyncio.gather(*(_answers(name, probe) for name, probe in probes.items()))
    return dict(zip(probes, answers, strict=True))


async def _answers(name: str, probe: Probe) -> bool:
    try:
        return bool(await asyncio.wait_for(probe(), PROBE_TIMEOUT_S))
    except Exception:
        logger.warning("dependency probe failed: %s", name, exc_info=True)
        return False


async def _ready(base_url: str, transport: httpx.AsyncBaseTransport | None) -> bool:
    """The service's ``/v1/readyz`` says ready (both services serve it at their root)."""
    root = base_url.rstrip("/").removesuffix("/v1")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, transport=transport) as client:
        return (await client.get(f"{root}/v1/readyz")).status_code == 200


def prometheus(up: dict[str, bool]) -> list[str]:
    lines = [
        "# HELP yal_dependency_up 1 while the dependency answers the supervisor, else 0.",
        "# TYPE yal_dependency_up gauge",
    ]
    lines += [f'yal_dependency_up{{dependency="{name}"}} {int(ok)}' for name, ok in up.items()]
    return lines
