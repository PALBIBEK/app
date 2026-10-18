"""Tool protocol and registry."""

from __future__ import annotations

import pytest

from githi.errors import ToolNotFound
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolContext, ToolResult
from githi.tools.registry import ToolRegistry, default_tools


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


def _echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="echo back",
        parameters={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        run=lambda args, ctx: ToolResult(content=args["text"]),
    )


class TestToolResult:
    def test_ok_by_default(self):
        assert ToolResult(content="x").is_error is False

    def test_error_result(self):
        assert ToolResult(content="bad", is_error=True).is_error is True


class TestRegistry:
    def test_register_and_get(self, ctx):
        reg = ToolRegistry([_echo_tool()])
        assert reg.get("echo").name == "echo"

    def test_unknown_tool_raises(self):
        with pytest.raises(ToolNotFound):
            ToolRegistry([]).get("nope")

    def test_names(self):
        assert ToolRegistry([_echo_tool()]).names() == ["echo"]

    def test_duplicate_names_rejected(self):
        """Silent shadowing would make which implementation runs depend on
        list order."""
        with pytest.raises(ValueError):
            ToolRegistry([_echo_tool(), _echo_tool()])

    def test_execute_returns_result(self, ctx):
        assert ToolRegistry([_echo_tool()]).execute("echo", {"text": "hi"}, ctx).text == "hi"

    def test_tool_exception_becomes_an_error_result(self, ctx):
        """A crashing tool must not kill the run -- the model sees the failure
        and can adapt. This is the single most important property here."""
        boom = Tool(name="boom", description="", parameters={},
                    run=lambda a, c: (_ for _ in ()).throw(RuntimeError("kaboom")))
        result = ToolRegistry([boom]).execute("boom", {}, ctx)
        assert result.is_error and "kaboom" in result.text

    def test_sandbox_violation_is_reported_not_raised(self, ctx):
        from githi.errors import SandboxViolation
        bad = Tool(name="bad", description="", parameters={},
                   run=lambda a, c: (_ for _ in ()).throw(SandboxViolation("escaped")))
        result = ToolRegistry([bad]).execute("bad", {}, ctx)
        assert result.is_error and "escaped" in result.text

    def test_unknown_tool_execute_is_an_error_result(self, ctx):
        """The model hallucinating a tool name is routine; it should get a
        correctable message, not an exception through the loop."""
        result = ToolRegistry([]).execute("ghost", {}, ctx)
        assert result.is_error and "ghost" in result.text


class TestSchemas:
    def test_wire_schema_shape(self):
        schema = ToolRegistry([_echo_tool()]).to_wire()[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert schema["function"]["parameters"]["required"] == ["text"]

    def test_empty_parameters_still_valid(self):
        t = Tool(name="t", description="d", parameters={}, run=lambda a, c: ToolResult(""))
        fn = ToolRegistry([t]).to_wire()[0]["function"]
        assert fn["parameters"]["type"] == "object"


class TestDefaultTools:
    def test_provides_pis_builtin_set(self, ctx):
        """Pi's docs list exactly these seven built-ins; parity is the goal.

        `find` and `ls` share a module here (both are path enumeration) but
        stay separate *tools*, because collapsing them would change the
        model-facing surface pi actually exposes.
        """
        assert set(ToolRegistry(default_tools()).names()) == {
            "bash", "read", "write", "edit", "grep", "find", "ls",
            "WebFetch", "WebSearch", "ask_user_question",
            "subagent", "subagent_fork", "subagent_continuable",
            "send_message", "interrupt_agent", "list_agents",
            "job_output", "job_kill",
        }

    def test_all_default_tools_have_descriptions_and_schemas(self):
        for tool in default_tools():
            assert tool.description.strip(), tool.name
            assert tool.parameters.get("type") == "object", tool.name
