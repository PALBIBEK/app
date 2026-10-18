"""GithiToolProvider — implements platform ToolProvider seam."""

from __future__ import annotations

import asyncio
import typing as t

from supervisor.agent.runtimes.githi.tools.base import (
    ToolError,
    ToolExecutionContext,
)
from supervisor.agent.runtimes.githi.tools.registry import get_registry
from supervisor.seams import ToolDescriptor, ToolInvokeContext, ToolResult

if t.TYPE_CHECKING:
    from supervisor.agent.runtimes.githi.tools.base import Tool
    from supervisor.seams.services import WorkspaceTreeIO


# Control-plane / identity fields the model must never inject into tools.
_STRIP_ARG_KEYS = frozenset(
    {
        "scope_id",
        "principal_id",
        "tenant_id",
        "working_directory",
        "session_id",
        "grant",
        "grants",
        "auth_token",
        "virtual_key",
    }
)


class GithiToolProvider:
    """Sync ToolProvider over the async githi Tool registry."""

    def __init__(
        self,
        session_id: str,
        *,
        working_directory: str = ".",
        timeout: int = 120,
        sandbox_enabled: bool = False,
        tree: "WorkspaceTreeIO | None" = None,
        on_mutate: t.Callable[[], None] | None = None,
    ) -> None:
        if sandbox_enabled and tree is None:
            raise ValueError("sandbox_enabled requires a WorkspaceTreeIO")
        self.session_id = session_id
        self.working_directory = working_directory
        self.timeout = timeout
        self.sandbox_enabled = sandbox_enabled
        self.tree = tree
        self.on_mutate = on_mutate
        self.registry = get_registry()

    def list_tools(self) -> list[ToolDescriptor]:
        out: list[ToolDescriptor] = []
        for tool in self.registry.get_all_tools().values():
            schema = tool.get_schema()
            out.append(
                ToolDescriptor(
                    name=tool.name,
                    description=str(schema.get("description") or ""),
                    parameters=dict(schema.get("input_schema") or {"type": "object"}),
                )
            )
        return out

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, t.Any],
    ) -> ToolResult:
        del ctx  # approvals / capability tokens reserved
        # Ignore model-supplied control-plane fields — session binds scope/identity.
        if isinstance(args, dict):
            args = {k: v for k, v in args.items() if k not in _STRIP_ARG_KEYS}

        tool = self.registry.get_tool(name)
        if tool is None:
            return ToolResult(ok=False, error=f"Tool {name!r} not found", code="NOT_FOUND")

        valid, error = tool.validate_args(args)
        if not valid:
            return ToolResult(
                ok=False,
                error=f"Invalid arguments for {name}: {error}",
                code="BAD_ARGS",
            )

        context = ToolExecutionContext(
            session_id=self.session_id,
            working_directory=self.working_directory,
            timeout=self.timeout,
            sandbox_enabled=self.sandbox_enabled,
            tree=self.tree,
            on_mutate=self.on_mutate,
        )

        try:
            payload = _run_async(lambda: tool.execute(args=args, context=context))
            return ToolResult(ok=True, payload=payload)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc), code="TOOL_ERROR")
        except Exception as exc:  # noqa: BLE001 — must not crash agent
            return ToolResult(ok=False, error=str(exc), code="INTERNAL")

    def get_tool(self, name: str) -> Tool | None:
        return self.registry.get_tool(name)


def _run_async(factory: t.Callable[[], t.Awaitable[t.Any]]) -> t.Any:
    """Run tool coroutine from sync ``invoke`` (AgentLoop uses to_thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    import concurrent.futures

    def _runner() -> t.Any:
        return asyncio.run(factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()
