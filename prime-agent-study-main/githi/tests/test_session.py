"""Session persistence (JSONL), and the system prompt builder."""

from __future__ import annotations

import pytest

from githi.harness.session import Session
from githi.harness.system_prompt import build_system_prompt
from githi.types import AssistantMessage, ToolCall, ToolResultMessage, UserMessage


class TestSession:
    def test_appends_and_reloads(self, tmp_path):
        s = Session(tmp_path / "s.jsonl")
        s.append(UserMessage("hi"))
        assert Session(tmp_path / "s.jsonl").load() == [UserMessage("hi")]

    def test_roundtrips_tool_calls(self, tmp_path):
        """Tool calls carry structured arguments; a lossy round trip would
        corrupt a resumed session in a way that only shows up later."""
        s = Session(tmp_path / "s.jsonl")
        original = AssistantMessage(
            content="", tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})])
        s.append(original)
        assert Session(tmp_path / "s.jsonl").load() == [original]

    def test_roundtrips_tool_results(self, tmp_path):
        s = Session(tmp_path / "s.jsonl")
        s.append(ToolResultMessage(tool_call_id="c1", content="out", is_error=True))
        loaded = Session(tmp_path / "s.jsonl").load()[0]
        assert loaded.tool_call_id == "c1" and loaded.is_error is True

    def test_missing_file_loads_empty(self, tmp_path):
        assert Session(tmp_path / "absent.jsonl").load() == []

    def test_jsonl_is_append_only(self, tmp_path):
        """One line per message, appended -- so a crash mid-run leaves every
        prior message intact rather than a truncated JSON document."""
        s = Session(tmp_path / "s.jsonl")
        s.append(UserMessage("a"))
        s.append(UserMessage("b"))
        assert len((tmp_path / "s.jsonl").read_text().strip().splitlines()) == 2

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        """A half-written final line after a crash must not make the whole
        session unreadable."""
        path = tmp_path / "s.jsonl"
        s = Session(path)
        s.append(UserMessage("good"))
        with path.open("a") as fh:
            fh.write("{not json\n")
        assert Session(path).load() == [UserMessage("good")]

    def test_creates_parent_directories(self, tmp_path):
        Session(tmp_path / "deep" / "s.jsonl").append(UserMessage("x"))
        assert (tmp_path / "deep" / "s.jsonl").exists()


class TestSystemPrompt:
    """Ported from pi's `buildSystemPrompt`: describe the tools, state two
    guidelines, name the working directory, stop."""

    def test_identifies_the_harness_and_the_job(self):
        assert "coding agent harness" in build_system_prompt().lower()

    def test_lists_available_tools_with_snippets(self):
        from githi.tools.registry import default_tools
        prompt = build_system_prompt(tools=default_tools())
        assert "Available tools:" in prompt
        assert "- bash:" in prompt and "- read:" in prompt

    def test_states_the_working_directory(self):
        assert "Current working directory: /w" in build_system_prompt(cwd="/w")

    def test_carries_pis_two_guidelines(self):
        prompt = build_system_prompt()
        assert "Be concise in your responses" in prompt
        assert "Show file paths clearly" in prompt

    def test_ask_user_guidance_lives_in_tool_description_not_guidelines(self):
        from githi.tools.ask_user import DESCRIPTION, ask_user_tool
        from githi.tools.registry import default_tools
        prompt = build_system_prompt(tools=default_tools())
        assert "instead of guessing" not in prompt
        assert "Prefer this over guessing" in DESCRIPTION
        assert "Prefer this over guessing" in ask_user_tool().description

    def test_appends_skill_listing(self):
        assert "<available_skills>" in build_system_prompt(
            skill_listing="<available_skills>\n</available_skills>")

    def test_appends_extra_context(self):
        assert "PROJECT RULES" in build_system_prompt(extra="PROJECT RULES")

    def test_custom_prompt_replaces_the_default(self):
        prompt = build_system_prompt(custom="Just do what I say.")
        assert "Just do what I say." in prompt
        assert "coding agent harness" not in prompt
        assert "Current working directory" in prompt, "context still appended"

    def test_omits_empty_sections_cleanly(self):
        assert "\n\n\n" not in build_system_prompt()

    def test_does_not_narrow_the_solution_space(self):
        """The regression guard for the bug that made this a port.

        An earlier hand-written prompt said authenticated calls would fail and
        concluded "work with what is on disk and what the task gives you". The
        model generalised that into "the network is closed" and stopped
        considering `git clone` -- failing a task three times while naming the
        correct command in its own answer. Pi's prompt mentions none of this
        and pi solved the same task first try. Guidance that forecloses an
        approach fails silently: nothing reports the option that was never
        considered."""
        prompt = build_system_prompt(cwd="/w").lower()
        for banned in ("work with what is on disk",
                       "network calls will fail",
                       "cannot succeed",
                       "do not waste a call"):
            assert banned not in prompt, f"prompt forecloses an approach: {banned!r}"
