"""Runtime factory - single entry point for creating AgentRuntime instances."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from supervisor.agent.seams import EMPTY_SEAMS, RuntimeSeams

if TYPE_CHECKING:
    from supervisor.agent.base import AgentRuntime

logger = logging.getLogger(__name__)


class RuntimeNames:
    """Well-known runtime identifiers."""
    ECHO = "echo"
    HARNESS = "harness"
    GITHI = "githi"


_KNOWN_RUNTIMES = frozenset({RuntimeNames.ECHO, RuntimeNames.HARNESS, RuntimeNames.GITHI})


def known_runtime_names() -> frozenset[str]:
    return _KNOWN_RUNTIMES


def create_runtime(
    runtime_name: str,
    *,
    seams: RuntimeSeams | None = None,
    model: str | None = None,
    provider: str | None = None,
    session_id: str = "",
    **kwargs: Any,
) -> AgentRuntime:
    """Create an agent runtime by name. Lazy imports for fast startup."""
    seams = seams or EMPTY_SEAMS
    logger.debug("creating runtime: name=%s model=%s provider=%s session=%s", runtime_name, model, provider, session_id)

    if runtime_name == RuntimeNames.ECHO:
        from supervisor.agent.runtimes.echo.runtime import EchoRuntime
        return EchoRuntime(seams=seams, session_id=session_id, **kwargs)

    if runtime_name == RuntimeNames.HARNESS:
        from supervisor.agent.runtimes.harness.runtime import HarnessRuntime
        kw: dict[str, Any] = {"seams": seams, "session_id": session_id, **kwargs}
        if model:
            kw["model"] = model
        if provider:
            kw["provider"] = provider
        return HarnessRuntime(**kw)

    if runtime_name == RuntimeNames.GITHI:
        from supervisor.agent.runtimes.githi.runtime import GithiRuntime

        if seams.tool_provider is None:
            from supervisor.seams.core.factory import build_standalone_tool_provider

            seams = replace(seams, tool_provider=build_standalone_tool_provider())
        kw = {"seams": seams, "session_id": session_id, **kwargs}
        if model:
            kw["model"] = model
        if provider:
            kw["provider"] = provider
        return GithiRuntime(**kw)

    raise ValueError(f"Unknown runtime: {runtime_name!r}. Available: {', '.join(sorted(_KNOWN_RUNTIMES))}")
