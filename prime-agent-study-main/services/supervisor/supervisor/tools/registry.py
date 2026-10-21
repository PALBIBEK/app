"""``ToolRegistry``: every tool implementation the supervisor can offer, by name.

The registry is what a pinned tool set resolves against. Built-in tools join
it by decorating their implementation::

    @tool(tool_class=ToolClass.PURE)
    async def read(args: ReadArgs, ctx: ToolContext) -> str:
        \"\"\"Read file contents from the workspace.\"\"\"

and extensions (HTTP tools) register at runtime with ``register``.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from supervisor.tools.errors import UnknownTools
from supervisor.tools.spec import Handler, ToolClass, ToolSpec

__all__ = ["ToolRegistry", "default_registry", "tool"]

_BUILTIN_PACKAGE = "supervisor.tools.builtin"


class ToolRegistry(Mapping[str, ToolSpec[Any]]):
    """An ordered, name-unique collection of ``ToolSpec``; read-only as a ``Mapping``."""

    __slots__ = ("_lock", "_specs", "name")

    def __init__(self, name: str, specs: Iterable[ToolSpec[Any]] = ()) -> None:
        self.name = name
        self._specs: dict[str, ToolSpec[Any]] = {}
        self._lock = threading.Lock()
        for spec in specs:
            self.register(spec)

    # Mapping ---------------------------------------------------------------

    def __getitem__(self, name: str) -> ToolSpec[Any]:
        return self._specs[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def __repr__(self) -> str:
        return f"ToolRegistry({self.name!r}, tools={list(self._specs)})"

    # Registration ----------------------------------------------------------

    def register(self, spec: ToolSpec[Any], *, replace: bool = False) -> ToolSpec[Any]:
        """Add ``spec``; a different implementation under a taken name is an error.

        Re-registering the same implementation (a module imported twice) is a
        no-op, so registration is safe to repeat.
        """
        with self._lock:
            existing = self._specs.get(spec.name)
            if existing is not None and not replace and existing.origin != spec.origin:
                raise ValueError(
                    f"tool {spec.name!r} is already registered by {existing.origin}; "
                    f"refusing {spec.origin} (pass replace=True to override)"
                )
            self._specs[spec.name] = spec
        return spec


    def tool[H: Handler[Any]](
        self,
        name: str | None = None,
        *,
        tool_class: ToolClass,
        needs_workspace: bool = True,
    ) -> Callable[[H], H]:
        """Decorator registering an ``async def handler(args, ctx)`` as a tool.

        The function is returned unchanged, so it stays directly callable and
        testable; only the registry holds the derived ``ToolSpec``.
        """

        def decorate(handler: H) -> H:
            self.register(
                ToolSpec.from_handler(
                    handler, name=name, tool_class=tool_class, needs_workspace=needs_workspace
                )
            )
            return handler

        return decorate

    # Views -----------------------------------------------------------------

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """A registry of exactly ``names``; raises ``UnknownTools`` naming every gap."""
        wanted = list(dict.fromkeys(names))
        missing = [name for name in wanted if name not in self._specs]
        if missing:
            raise UnknownTools(missing, registry=self.name)
        return ToolRegistry(self.name, (self._specs[name] for name in wanted))

    def definitions(self) -> list[dict[str, Any]]:
        """Catalog ``ToolDefinition`` bodies for every tool, in registration order."""
        return [spec.definition() for spec in self._specs.values()]


_DEFAULT = ToolRegistry("default")

tool = _DEFAULT.tool
"""Register into the process-wide registry (what ``default_registry()`` returns)."""


def default_registry() -> ToolRegistry:
    """The process-wide registry, with every built-in tool loaded."""
    importlib.import_module(_BUILTIN_PACKAGE)
    return _DEFAULT
