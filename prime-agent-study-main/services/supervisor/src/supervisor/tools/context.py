"""What a tool knows about the call it is serving."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from supervisor.tools.errors import WorkspaceUnavailable
from supervisor.tools.workspace import HostWorkspace, TreeWorkspace, Workspace

if TYPE_CHECKING:
    from supervisor.seams.core.types import ToolInvokeContext

__all__ = ["DEFAULT_TIMEOUT_S", "ToolContext"]

DEFAULT_TIMEOUT_S: Final = 120.0


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext:
    """Session identity, the workspace to act on, and the call's time budget.

    Identity comes only from the supervisor. Tools never read it from model
    arguments, which the seam strips of control-plane keys before parsing.
    """

    session_id: str | None = None
    tenant_id: str | None = None
    trace_id: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    on_mutate: Callable[[], None] | None = None
    bound_workspace: Workspace | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    """Variables the session grants this call's commands (the browse runner's access)."""

    @property
    def workspace(self) -> Workspace:
        """The session's workspace; tools that need none (``echo``) never touch it."""
        if self.bound_workspace is None:
            raise WorkspaceUnavailable("no workspace is bound to this tool call")
        return self.bound_workspace

    def mutated(self) -> None:
        """Tell the session its workspace changed (drives sandbox → store sync)."""
        if self.on_mutate is not None:
            self.on_mutate()

    @classmethod
    def from_invoke(
        cls,
        ctx: ToolInvokeContext,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> ToolContext:
        """Build from the seam's opaque ``ToolInvokeContext``.

        ``extra`` may carry, most specific first: a ``tree`` (the session runs
        in a sandbox), a ``working_directory`` on the host, or just the
        ``session_id`` whose host workspace is then used; and an ``env`` for the
        commands the call runs.
        """
        extra: Mapping[str, Any] = ctx.extra or {}
        session_id = _text(extra.get("session_id"))
        return cls(
            session_id=session_id,
            tenant_id=_text(extra.get("tenant_id")),
            trace_id=ctx.trace_id,
            timeout_s=timeout_s,
            on_mutate=extra.get("on_mutate"),
            bound_workspace=_workspace(extra, session_id),
            env=dict(extra.get("env") or {}),
        )


def _text(value: object) -> str | None:
    return str(value) if value else None


def _workspace(extra: Mapping[str, Any], session_id: str | None) -> Workspace | None:
    match extra:
        case {"workspace": Workspace() as workspace}:
            return workspace
        case {"tree": tree} if tree is not None:
            return TreeWorkspace(tree)
        case {"working_directory": str(directory)} if directory not in ("", "."):
            return HostWorkspace(directory)
    if session_id is None:
        return None
    from supervisor.session.workspaces import session_workspace  # session imports seams

    return HostWorkspace(session_workspace(session_id))
