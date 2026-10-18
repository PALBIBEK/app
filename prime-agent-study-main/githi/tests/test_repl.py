"""The interactive loop.

Driven with `StringIO` for stdin and stdout, so the whole REPL is testable
without a terminal. That is worth the small awkwardness: the alternative is a
component only checkable by hand, which in practice means not checked.

The behaviours that matter are the ones a user would notice immediately and a
happy-path test would miss: Ctrl-D exits rather than hanging, a blank line is
not sent to the model, a crash mid-turn does not end the session, and the
interrupt flag is cleared before each run.

That last one is the subtle one. A flag left set by the previous interrupt
aborts the next run before it sends a single request -- the agent appears to
ignore you, with nothing on screen to explain why.
"""

from __future__ import annotations

import io

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.interrupt import Interrupt
from githi.llm.client import Completion
from githi.repl import InFlightInput, Renderer, _brief, _format_markdown, _run_command, run_repl
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.agent.events import Event, EventType
from githi.types import AssistantMessage, StopReason, ToolCall, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="ok")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _agent(tmp_path, script=None, **cfg):
    return Agent(llm=ScriptedLLM(script or [AssistantMessage(content="hello")]),
                 tools=ToolRegistry([Tool(name="bash", description="d",
                                          parameters={"type": "object"},
                                          run=lambda a, c: ToolResult(content="ran"))]),
                 policy=SandboxPolicy(root=tmp_path), config=AgentConfig(**cfg))


def _run(tmp_path, typed: str, script=None, interrupt=None, **cfg):
    out = io.StringIO()
    flag = interrupt or Interrupt()
    agent = _agent(tmp_path, script, interrupt=flag, **cfg)
    code = run_repl(agent, flag, stdin=io.StringIO(typed), stdout=out)
    return agent, out.getvalue(), code


class TestSessionControl:
    def test_eof_exits_cleanly(self, tmp_path):
        _, _, code = _run(tmp_path, "")
        assert code == 0

    def test_a_prompt_reaches_the_agent(self, tmp_path):
        agent, _, _ = _run(tmp_path, "find the bug\n")
        assert any("find the bug" in (getattr(m, "content", "") or "")
                   for m in agent.state.messages)

    def test_blank_lines_are_not_sent(self, tmp_path):
        """Pressing enter on an empty prompt must not cost an API call."""
        agent, _, _ = _run(tmp_path, "\n\n\n")
        assert agent.llm.calls == []

    def test_exit_command_quits(self, tmp_path):
        agent, _, code = _run(tmp_path, "exit\n")
        assert code == 0 and agent.llm.calls == []

    def test_direct_bash_does_not_enter_the_agent_transcript(self, tmp_path):
        agent, text, code = _run(tmp_path, "!echo direct-output\n")
        assert code == 0
        assert "direct-output" in text
        assert agent.llm.calls == []
        assert not any("!echo" in (getattr(message, "content", "") or "")
                       for message in agent.state.messages)

    def test_direct_bash_reports_nonzero_exit_without_crashing_the_repl(self, tmp_path):
        agent, text, code = _run(tmp_path, "!echo bad >&2; exit 7\n")
        assert code == 0
        assert "bad" in text and "7" in text
        assert agent.llm.calls == []

    def test_several_turns_in_one_session(self, tmp_path):
        agent, _, _ = _run(tmp_path, "one\ntwo\n",
                           script=[AssistantMessage(content="a"),
                                   AssistantMessage(content="b")])
        assert len(agent.llm.calls) == 2

    def test_the_conversation_carries_across_turns(self, tmp_path):
        agent, _, _ = _run(tmp_path, "one\ntwo\n",
                           script=[AssistantMessage(content="a"),
                                   AssistantMessage(content="b")])
        assert any("one" in (getattr(m, "content", "") or "")
                   for m in agent.llm.calls[-1])

    def test_slash_new_resets_the_conversation(self, tmp_path):
        agent = _agent(tmp_path)
        agent.run("old context")
        out = io.StringIO()
        assert not _run_command("/new", agent, Renderer(out=out, colour=False))
        assert agent.state.messages == []
        assert "new conversation" in out.getvalue()

    def test_slash_compact_and_model_are_routed_to_agent(self, tmp_path):
        agent = _agent(tmp_path)
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        assert not _run_command("/compact", agent, renderer)
        assert not _run_command("/model test-model-2", agent, renderer)
        assert agent.model == "test-model-2"
        assert "compacted" in out.getvalue() and "test-model-2" in out.getvalue()

    def test_slash_exit_requests_a_clean_exit(self, tmp_path):
        assert _run_command("/exit", _agent(tmp_path), Renderer(out=io.StringIO(), colour=False))

    def test_session_commands_show_tree_mark_and_rewind(self, tmp_path):
        path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=path)
        agent.run("first")
        renderer = Renderer(out=io.StringIO(), colour=False)

        assert not _run_command("/mark before-change", agent, renderer)
        mark = agent.mark()
        agent.run("second")
        assert not _run_command("/tree", agent, renderer)
        assert not _run_command(f"/rewind {mark[:8]}", agent, renderer)
        assert "before-change" in renderer.out.getvalue()
        assert not any("second" in (getattr(m, "content", "") or "")
                       for m in agent.state.messages)

    def test_session_and_new_commands_report_the_active_session(self, tmp_path):
        path = tmp_path / "session.jsonl"
        agent = _agent(tmp_path, session_path=path)
        agent.run("first")
        renderer = Renderer(out=io.StringIO(), colour=False)

        assert not _run_command("/session", agent, renderer)
        assert not _run_command("/new", agent, renderer)
        assert agent.session_path != path
        assert "session" in renderer.out.getvalue().lower()


class TestResilience:
    def test_a_crash_does_not_end_the_session(self, tmp_path):
        """A provider blip on turn one must not discard the session."""
        class Exploding:
            model = "m"
            calls: list = []

            def complete(self, messages, tools, **kw):
                raise RuntimeError("provider exploded")

        out = io.StringIO()
        agent = Agent(llm=Exploding(), tools=ToolRegistry([]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        code = run_repl(agent, Interrupt(), stdin=io.StringIO("go\nagain\n"),
                        stdout=out)
        assert code == 0 and "provider exploded" in out.getvalue()

    def test_the_interrupt_flag_is_cleared_before_each_run(self, tmp_path):
        """Otherwise a stale flag silently aborts the next run."""
        flag = Interrupt()
        flag.set()
        agent, _, _ = _run(tmp_path, "go\n", interrupt=flag)
        assert agent.llm.calls, "a stale interrupt flag suppressed the run"


class TestOutput:
    def test_the_answer_is_shown(self, tmp_path):
        _, text, _ = _run(tmp_path, "go\n",
                          script=[AssistantMessage(content="the answer")])
        assert "the answer" in text or "[done]" in text

    def test_a_status_line_follows_each_turn(self, tmp_path):
        _, text, _ = _run(tmp_path, "go\n")
        assert "turns" in text and "tokens" in text

    def test_an_interrupted_run_says_so(self, tmp_path):
        flag = Interrupt()
        agent = _agent(tmp_path, [AssistantMessage(content="", tool_calls=[
            ToolCall("c1", "bash", {}), ToolCall("c2", "bash", {})])],
            interrupt=flag)
        agent.tools.get("bash").run = lambda a, c: (flag.set(),
                                                    ToolResult(content="ran"))[1]
        out = io.StringIO()
        run_repl(agent, flag, stdin=io.StringIO("go\n"), stdout=out)
        assert "interrupted" in out.getvalue().lower()

    def test_the_banner_mentions_esc(self, tmp_path):
        _, text, _ = _run(tmp_path, "")
        assert "ESC" in text

    def test_no_escape_codes_when_output_is_redirected(self, tmp_path):
        """StringIO is not a TTY. Control codes in a redirected transcript are
        the same defect the bash sanitiser exists to prevent -- and the banner
        and prompt bypassed the colour check until this caught them."""
        _, text, _ = _run(tmp_path, "go\n")
        assert "\x1b[" not in text


class TestRenderer:
    def test_deltas_are_written_as_they_arrive(self):
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        for piece in ("Hel", "lo"):
            renderer.on_event(Event(EventType.TEXT_DELTA, {"delta": piece}))
        renderer.flush_text()
        assert out.getvalue() == "Hello"

    def test_a_tool_call_breaks_the_streamed_line(self):
        """Without the break, the tool line lands mid-sentence."""
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        renderer.on_event(Event(EventType.TEXT_DELTA, {"delta": "thinking"}))
        renderer.on_event(Event(EventType.TOOL_START,
                                {"tool": "bash", "args": {"command": "ls"}}))
        assert out.getvalue().startswith("thinking\n")

    def test_the_tool_name_and_argument_are_shown(self):
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        renderer.on_event(Event(EventType.TOOL_START,
                                {"tool": "bash", "args": {"command": "git log"}}))
        assert "bash" in out.getvalue() and "git log" in out.getvalue()

    def test_colour_can_be_disabled_for_pipes(self):
        out = io.StringIO()
        Renderer(out=out, colour=False).on_event(
            Event(EventType.TOOL_START, {"tool": "bash", "args": {}}))
        assert "\x1b[" not in out.getvalue()

    def test_turn_and_memory_events_are_visible(self):
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        renderer.on_event(Event(EventType.TURN_START, {"turn": 2}))
        renderer.on_event(Event(EventType.MEMORY_RECALL, {"count": 3}))
        renderer.on_event(Event(EventType.TURN_END, {"turn": 2, "tool_calls": 1}))
        assert "turn 2" in out.getvalue() and "recalled 3" in out.getvalue()

    def test_markdown_formatter_preserves_code_and_marks_structure(self):
        text = _format_markdown("# Title\n- item with `code`\n```py\nx = 1\n```", colour=False)
        assert "Title" in text and "• item with code" in text and "x = 1" in text

    def test_tool_output_can_be_expanded_after_completion(self):
        out = io.StringIO()
        renderer = Renderer(out=out, colour=False)
        renderer.on_event(Event(EventType.TOOL_END, {
            "tool": "bash", "result": "first line\nsecond line", "tool_id": "call-1",
        }))
        assert "first line" in out.getvalue()
        assert renderer.expand("call-1") is True
        assert "second line" in out.getvalue()


class TestInFlightInput:
    def test_enter_queues_a_steering_message(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="done")])
        collector = InFlightInput(agent, Interrupt(), Renderer(out=io.StringIO(), colour=False))
        for key in "change direction\n":
            assert collector.on_key(key)
        assert agent._steering.has_items()
        agent.run("start")
        assert any("change direction" in (getattr(message, "content", "") or "")
                   for message in agent.llm.calls[-1])

    def test_alt_enter_queues_a_follow_up_message(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="done")])
        collector = InFlightInput(agent, Interrupt(), Renderer(out=io.StringIO(), colour=False))
        for key in "continue later":
            collector.on_key(key)
        collector.on_key("\x1b")
        collector.on_key("\n")
        assert agent._follow_up.has_items()
        agent.run("start")
        assert any("continue later" in (getattr(message, "content", "") or "")
                   for message in agent.llm.calls[-1])
        collector.close()

    def test_second_escape_escalates_after_terminal_cleanup(self, tmp_path):
        interrupt = Interrupt()
        collector = InFlightInput(_agent(tmp_path), interrupt,
                                  Renderer(out=io.StringIO(), colour=False))
        collector.on_key("\x1b")
        collector.on_key("\x1b")
        assert interrupt.count == 2
        collector.close()


class TestBrief:
    def test_it_prefers_the_command(self):
        assert _brief({"command": "git log", "other": "x"}) == "git log"

    def test_long_arguments_are_elided(self):
        assert len(_brief({"command": "x" * 500})) < 80

    def test_newlines_are_flattened(self):
        """A multi-line command must stay on one line, or the tool display
        pushes the conversation off the screen."""
        assert "\n" not in _brief({"command": "line one\nline two"})

    def test_unknown_shapes_are_empty(self):
        assert _brief({"weird": 1}) == "" and _brief(None) == ""


class TestAskChoiceParse:
    def test_single_select_rejects_multiple_numbers(self):
        from githi.errors import AskUserError
        from githi.repl import _parse_ask_choice
        from githi.tools.ask_user import AskOption, AskQuestion

        q = AskQuestion(
            id="q", question="?",
            options=[AskOption("Python"), AskOption("Node")])
        with pytest.raises(AskUserError) as caught:
            _parse_ask_choice(q, "2 2", object(), None)
        assert caught.value.code == "BAD_ANSWER"

    def test_single_select_accepts_one_number(self):
        from githi.repl import _parse_ask_choice
        from githi.tools.ask_user import AskOption, AskQuestion

        q = AskQuestion(
            id="q", question="?",
            options=[AskOption("Python"), AskOption("Node")])
        item = _parse_ask_choice(q, "2", object(), None)
        assert item.selected == ["Node"]
