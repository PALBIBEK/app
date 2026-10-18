"""RegistryToolProvider — the ``ToolProvider`` seam over ``supervisor.tools``.

A session is offered exactly the tools its composition pinned. The pinned
``ToolDescriptor`` (name, description, schema) is what the model sees, since
that is what was published and digested; the registry supplies the code that
runs. Binding fails up front when a pinned name has no implementation, so a
session never starts with a tool that can only fail once called.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult
from supervisor.tools import (
    DEFAULT_TIMEOUT_S,
    ToolContext,
    ToolError,
    ToolNotFound,
    ToolSpec,
    UnknownTools,
    default_registry,
)

__all__ = ["CONTROL_PLANE_KEYS", "RegistryToolProvider"]

_log = logging.getLogger(__name__)

CONTROL_PLANE_KEYS: Final = frozenset(
    {
        "auth_token",
        "grant",
        "grants",
        "principal_id",
        "scope_id",
        "session_id",
        "tenant_id",
        "virtual_key",
        "working_directory",
    }
)
"""Identity and placement come from the supervisor; a model naming them is ignored."""


class RegistryToolProvider:
    """Provider: list/invoke the offered subset of a ``ToolRegistry``."""

    __slots__ = ("_offered", "_specs", "_timeout_s")

    def __init__(
        self,
        offered: Iterable[ToolDescriptor] | None = None,
        *,
        registry: Mapping[str, ToolSpec[Any]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Offer ``offered`` (default: every registered tool, as the registry describes it).

        Raises ``UnknownTools`` naming every offered tool without an implementation.
        """
        specs = registry if registry is not None else default_registry()
        descriptors = (
            list(offered) if offered is not None else [_describe(s) for s in specs.values()]
        )
        _require(specs, (d.name for d in descriptors))
        self._offered: dict[str, ToolDescriptor] = {d.name: d for d in descriptors}
        self._specs = {name: specs[name] for name in self._offered}
        self._timeout_s = timeout_s

    @classmethod
    def of(
        cls,
        *names: str,
        registry: Mapping[str, ToolSpec[Any]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> RegistryToolProvider:
        """Offer the named tools as the registry itself describes them."""
        specs = registry if registry is not None else default_registry()
        _require(specs, names)
        return cls([_describe(specs[name]) for name in names], registry=specs, timeout_s=timeout_s)

    def __repr__(self) -> str:
        return f"RegistryToolProvider({list(self._offered)})"

    # ToolProvider ----------------------------------------------------------

    def list_tools(self) -> list[ToolDescriptor]:
        return list(self._offered.values())

    def invoke(self, ctx: ToolInvokeContext, name: str, args: dict[str, Any]) -> ToolResult:
        """Run one call to completion; every failure becomes a typed ``ToolResult``."""
        try:
            spec = self._specs.get(name)
            if spec is None:
                raise ToolNotFound(name)
            context = ToolContext.from_invoke(ctx, timeout_s=self._timeout_s)
            payload = _run_blocking(lambda: spec.run(_scrub(args), context))
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc), code=exc.code)
        except Exception as exc:  # noqa: BLE001 — a tool bug must not end the agent's run
            _log.exception("tool %s crashed", name)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}", code="TOOL_INTERNAL")
        return ToolResult(ok=True, payload=payload)


def _describe(spec: ToolSpec[Any]) -> ToolDescriptor:
    return ToolDescriptor(name=spec.name, description=spec.description, parameters=spec.parameters)


def _require(registry: Mapping[str, ToolSpec[Any]], names: Iterable[str]) -> None:
    if missing := [name for name in names if name not in registry]:
        raise UnknownTools(missing, registry=getattr(registry, "name", "tool"))


def _scrub(args: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (args or {}).items() if k not in CONTROL_PLANE_KEYS}


def _run_blocking[T](factory: Callable[[], Awaitable[T]]) -> T:
    """Drive a coroutine from the seam's synchronous ``invoke``.

    Callers normally reach ``invoke`` through ``asyncio.to_thread``, where no
    loop is running. Inside a running loop ``asyncio.run`` cannot nest, so the
    coroutine gets a loop of its own on a short-lived thread while the caller waits.
    """

    async def main() -> T:
        return await factory()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(main())
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool") as pool:
        return pool.submit(asyncio.run, main()).result()
