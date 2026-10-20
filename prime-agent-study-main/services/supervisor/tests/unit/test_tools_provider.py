"""RegistryToolProvider: the ToolProvider seam over the registry, and catalog binding."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.providers import bind_tool_set
from supervisor.catalog.services import require_implemented
from supervisor.catalog.storage import tool_set_from_dict
from supervisor.seams import ToolProvider
from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext
from supervisor.seams.providers.tools import RegistryToolProvider
from supervisor.tools import (
    HostWorkspace,
    PathEscape,
    ToolArgs,
    ToolClass,
    ToolContext,
    ToolRegistry,
    UnknownTools,
    default_registry,
)


class Nothing(ToolArgs):
    pass


@pytest.fixture
def ctx(tmp_path: Path) -> ToolInvokeContext:
    (tmp_path / "a.txt").write_text("alpha")
    return ToolInvokeContext(extra={"working_directory": str(tmp_path), "session_id": "s1"})


def test_is_a_tool_provider() -> None:
    assert isinstance(RegistryToolProvider.of("read"), ToolProvider)


def test_offers_exactly_what_was_asked_for(ctx: ToolInvokeContext) -> None:
    provider = RegistryToolProvider.of("read", "ls")
    assert [t.name for t in provider.list_tools()] == ["read", "ls"]
    result = provider.invoke(ctx, "write", {"file_path": "x", "content": "y"})
    assert (result.ok, result.code) == (False, "TOOL_NOT_FOUND")


def test_pinned_descriptor_is_what_the_model_sees(ctx: ToolInvokeContext) -> None:
    pinned = ToolDescriptor(
        name="read",
        description="Read a file from the workspace",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    provider = RegistryToolProvider([pinned])
    assert provider.list_tools() == [pinned]
    assert provider.invoke(ctx, "read", {"path": "a.txt"}).payload == "alpha"


def test_offering_an_unimplemented_tool_fails_at_construction() -> None:
    with pytest.raises(UnknownTools, match="weather"):
        RegistryToolProvider.of("read", "weather")


def test_failures_come_back_as_typed_results(ctx: ToolInvokeContext) -> None:
    provider = RegistryToolProvider.of("read")
    cases = {
        "BAD_ARGS": {},
        "PATH_ESCAPE": {"file_path": "../../etc/passwd"},
        "FILE_NOT_FOUND": {"file_path": "missing.txt"},
    }
    for code, args in cases.items():
        result = provider.invoke(ctx, "read", args)
        assert (result.ok, result.code) == (False, code), result
        assert result.error


def test_model_cannot_redirect_the_call_through_control_plane_keys(
    ctx: ToolInvokeContext, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.txt").write_text("not yours")
    result = RegistryToolProvider.of("read").invoke(
        ctx, "read", {"file_path": "a.txt", "working_directory": str(elsewhere), "session_id": "s2"}
    )
    assert result.payload == "alpha"


def _registry_with(handler: object) -> ToolRegistry:
    registry = ToolRegistry("test")
    registry.tool("probe", tool_class=ToolClass.PURE)(handler)  # type: ignore[arg-type]
    return registry


def test_a_crashing_tool_does_not_crash_the_run(ctx: ToolInvokeContext) -> None:
    async def probe(args: Nothing, ctx: ToolContext) -> None:
        """Crash."""
        raise RuntimeError("bug in tool")

    result = RegistryToolProvider(registry=_registry_with(probe)).invoke(ctx, "probe", {})
    assert (result.ok, result.code, result.error) == (
        False,
        "TOOL_INTERNAL",
        "RuntimeError: bug in tool",
    )


def test_a_hung_tool_times_out(ctx: ToolInvokeContext) -> None:
    async def probe(args: Nothing, ctx: ToolContext) -> None:
        """Hang."""
        await asyncio.sleep(30)

    provider = RegistryToolProvider(registry=_registry_with(probe), timeout_s=0.05)
    result = provider.invoke(ctx, "probe", {})
    assert (result.ok, result.code) == (False, "TOOL_TIMEOUT")


def test_identity_reaches_the_tool_from_the_context_only(ctx: ToolInvokeContext) -> None:
    seen: list[ToolContext] = []

    async def probe(args: Nothing, ctx: ToolContext) -> None:
        """Record."""
        seen.append(ctx)

    invoke_ctx = ToolInvokeContext(trace_id="t-1", extra={"session_id": "s1", "tenant_id": "acme"})
    RegistryToolProvider(registry=_registry_with(probe)).invoke(
        invoke_ctx, "probe", {"tenant_id": "evil"}
    )
    assert (seen[0].session_id, seen[0].tenant_id, seen[0].trace_id) == ("s1", "acme", "t-1")


async def test_invoke_works_from_inside_a_running_loop(ctx: ToolInvokeContext) -> None:
    assert (
        RegistryToolProvider.of("read").invoke(ctx, "read", {"file_path": "a.txt"}).payload
        == "alpha"
    )


# --- workspace confinement on the host -----------------------------------------


def test_host_paths_resolve_inside_the_root(tmp_path: Path) -> None:
    workspace = HostWorkspace(tmp_path)
    assert workspace.relpath(str(tmp_path / "src" / "a.py")) == "src/a.py"
    assert workspace.relpath("/workspace/src/a.py") == "src/a.py"
    assert workspace.relpath(".") == ""


def test_symlinks_out_of_the_workspace_are_refused(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("x")
    (root / "door").symlink_to(outside)
    with pytest.raises(PathEscape):
        HostWorkspace(root).relpath("door/secret")


# --- catalog binding -------------------------------------------------------------


def test_binding_a_tool_set_offers_its_pinned_tools() -> None:
    definitions = {name: default_registry()[name].definition() for name in ("write", "read")}
    provider = bind_tool_set(
        tool_set_from_dict({"name": "ts", "semver": "1.0.0", "definitions": definitions})
    )
    assert [t.name for t in provider.list_tools()] == ["read", "write"]


def test_binding_or_publishing_an_unimplemented_tool_is_refused() -> None:
    tool_set = tool_set_from_dict(
        {
            "name": "ts",
            "semver": "1.0.0",
            "definitions": {
                "read": default_registry()["read"].definition(),
                "weather": {
                    "name": "weather",
                    "description": "w",
                    "parameters": {"type": "object"},
                },
            },
        }
    )
    with pytest.raises(InvalidArtifact, match="weather"):
        bind_tool_set(tool_set)
    with pytest.raises(InvalidArtifact, match="weather"):
        require_implemented(tool_set)
