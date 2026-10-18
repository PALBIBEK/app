"""ToolSpec derivation and ToolRegistry semantics."""

from __future__ import annotations

import pytest
from pydantic import Field

from supervisor.tools import (
    ToolArgs,
    ToolClass,
    ToolContext,
    ToolRegistry,
    ToolSpec,
    UnknownTools,
    default_registry,
)

BUILTINS = {"bash", "echo", "edit", "git_commit", "grep", "ls", "read", "remember", "write"}


class GreetArgs(ToolArgs):
    name: str
    """Who to greet"""

    times: int | None = Field(default=None, ge=1)
    """Repeat count (default: 1)"""


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry("test")


def test_decorator_derives_the_model_facing_contract_from_code(registry: ToolRegistry) -> None:
    @registry.tool(tool_class=ToolClass.PURE)
    async def greet(args: GreetArgs, ctx: ToolContext) -> str:
        """Greet someone by name.

        Developer notes after the first paragraph are not shown to the model.
        """
        return f"hello {args.name}" * (args.times or 1)

    spec = registry["greet"]
    assert spec.description == "Greet someone by name."
    assert spec.tool_class is ToolClass.PURE
    assert spec.origin.endswith(
        ":test_decorator_derives_the_model_facing_contract_from_code.<locals>.greet"
    )
    # No titles, no null unions, no defaults: optional simply means not required.
    assert spec.parameters == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to greet"},
            "times": {"type": "integer", "minimum": 1, "description": "Repeat count (default: 1)"},
        },
        "required": ["name"],
    }
    assert spec.definition() == {
        "name": "greet",
        "description": "Greet someone by name.",
        "parameters": spec.parameters,
        "tool_class": "pure",
    }


def test_decorator_returns_the_function_unchanged(registry: ToolRegistry) -> None:
    async def greet(args: GreetArgs, ctx: ToolContext) -> str:
        """Greet."""
        return args.name

    assert registry.tool(tool_class=ToolClass.PURE)(greet) is greet


def test_parameters_are_a_fresh_copy(registry: ToolRegistry) -> None:
    @registry.tool(tool_class=ToolClass.PURE)
    async def greet(args: GreetArgs, ctx: ToolContext) -> str:
        """Greet."""
        return args.name

    registry["greet"].parameters["properties"].clear()
    assert "name" in registry["greet"].parameters["properties"]


async def _no_docstring(args: GreetArgs, ctx: ToolContext) -> None:
    pass


def _sync(args: GreetArgs, ctx: ToolContext) -> None:
    """Sync."""


async def _unannotated(args, ctx) -> None:
    """Unannotated."""


async def _wrong_arity(args: GreetArgs) -> None:
    """Wrong arity."""


@pytest.mark.parametrize(
    ("handler", "problem"),
    [
        (_sync, "must be an async function"),
        (_no_docstring, "needs a docstring"),
        (_unannotated, "annotate 'args' with a pydantic model"),
        (_wrong_arity, "exactly (args, ctx)"),
    ],
)
def test_malformed_tools_are_rejected_at_registration(handler: object, problem: str) -> None:
    with pytest.raises(TypeError, match=problem.replace("(", r"\(").replace(")", r"\)")):
        ToolSpec.from_handler(handler, tool_class=ToolClass.PURE)  # type: ignore[arg-type]


def test_a_name_cannot_be_taken_by_a_second_implementation(registry: ToolRegistry) -> None:
    @registry.tool("greet", tool_class=ToolClass.PURE)
    async def first(args: GreetArgs, ctx: ToolContext) -> str:
        """First."""
        return ""

    async def second(args: GreetArgs, ctx: ToolContext) -> str:
        """Second."""
        return ""

    with pytest.raises(ValueError, match="already registered"):
        registry.tool("greet", tool_class=ToolClass.PURE)(second)
    # The same implementation again (a module imported twice) is a no-op.
    registry.register(ToolSpec.from_handler(first, name="greet", tool_class=ToolClass.PURE))
    registry.register(
        ToolSpec.from_handler(second, name="greet", tool_class=ToolClass.PURE), replace=True
    )
    assert registry["greet"].handler is second


def test_subset_names_every_missing_tool() -> None:
    builtins = default_registry()
    assert list(builtins.subset(["read", "grep"])) == ["read", "grep"]
    with pytest.raises(UnknownTools) as caught:
        builtins.subset(["read", "weather", "teleport"])
    assert caught.value.names == ("teleport", "weather")
    assert caught.value.code == "TOOL_NOT_IMPLEMENTED"


def test_default_registry_holds_every_builtin() -> None:
    registry = default_registry()
    assert set(registry) >= BUILTINS
    assert registry is default_registry()  # process-wide, loaded once
    for name in BUILTINS:
        spec = registry[name]
        assert spec.origin.startswith(f"supervisor.tools.builtin.{name}:")
        assert spec.description and spec.parameters["type"] == "object"
