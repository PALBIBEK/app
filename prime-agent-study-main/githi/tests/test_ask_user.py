"""ask_user_question tool — Claude / DeepSeek clarifying questions."""

from __future__ import annotations

import json

from githi.errors import AskUserError
from githi.sandbox.policy import SandboxPolicy
from githi.tools.ask_user import (
    DESCRIPTION,
    OUTPUT_SCHEMA,
    AskAnswer,
    AskAnswerItem,
    ask_user_tool,
    enforce_output_payload,
)
from githi.tools.base import ToolContext
from githi.tools.registry import ToolRegistry


def _ctx(**extra):
    return ToolContext(policy=SandboxPolicy(root="/tmp"), extra=dict(extra))


def _questions(**kwargs):
    base = {
        "id": "q1",
        "question": "Ship it?",
        "options": [{"label": "Yes"}, {"label": "No"}],
    }
    base.update(kwargs)
    return {"questions": [base]}


def _code(result) -> str | None:
    details = result.details or {}
    return details.get("code") if isinstance(details, dict) else None


class TestAskUserTool:
    def test_description_says_when_to_use(self):
        assert "Prefer this over guessing" in DESCRIPTION
        assert "stable id" in DESCRIPTION
        tool = ask_user_tool()
        assert tool.output_schema == OUTPUT_SCHEMA
        assert "Prefer this over guessing" in tool.to_wire()["function"]["description"]

    def test_delegated_caller_beats_provider(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=["Yes"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(),
            _ctx(delegated=True, ask_user=ask))
        assert result.is_error
        assert _code(result) == "DELEGATED_CALLER"
        assert result.text.startswith("Error [DELEGATED_CALLER]:")

    def test_no_ui_is_typed_no_provider(self):
        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx())
        assert result.is_error
        assert _code(result) == "NO_PROVIDER"
        assert result.text.startswith("Error [NO_PROVIDER]:")
        assert "no UI" in result.text

    def test_empty_questions_is_typed(self):
        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", {"questions": []}, _ctx(
                ask_user=lambda qs: AskAnswer(answers=[])))
        assert result.is_error
        assert _code(result) == "EMPTY_QUESTIONS"
        assert result.text.startswith("Error [EMPTY_QUESTIONS]:")

    def test_option_is_returned_as_json(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=["Yes"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert not result.is_error
        payload = json.loads(result.text)
        assert payload["answers"][0]["selected"] == ["Yes"]
        assert result.details["schema"] == OUTPUT_SCHEMA

    def test_custom_answer_is_returned(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=[], custom="maybe later")])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert not result.is_error
        payload = json.loads(result.text)
        assert payload["answers"][0]["custom"] == "maybe later"

    def test_interrupt_before_ask_is_aborted(self):
        class Flag:
            def is_set(self):
                return True

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(),
            _ctx(interrupt=Flag(), ask_user=lambda qs: AskAnswer(answers=[])))
        assert result.is_error
        assert _code(result) == "ASK_ABORTED"
        assert result.text.startswith("Error [ASK_ABORTED]:")

    def test_cancel_is_not_ui_error(self):
        def ask(_qs):
            raise AskUserError("the user cancelled ask_user_question", "ASK_CANCELLED")

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert result.is_error
        assert _code(result) == "ASK_CANCELLED"
        assert result.text.startswith("Error [ASK_CANCELLED]:")

    def test_asker_exception_is_ui_error(self):
        def ask(_qs):
            raise RuntimeError("ui broken")

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert result.is_error
        assert _code(result) == "UI_ERROR"
        assert result.text.startswith("Error [UI_ERROR]:")
        assert "ui broken" in result.text

    def test_bad_answer_id_is_typed(self):
        def ask(_qs):
            return AskAnswer(answers=[
                AskAnswerItem(id="other", selected=["Yes"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert result.is_error
        assert _code(result) == "BAD_ANSWER"
        assert result.text.startswith("Error [BAD_ANSWER]:")

    def test_enforce_output_payload_rejects_extra_fields(self):
        try:
            enforce_output_payload({"answers": [], "extra": True})
            raise AssertionError("expected AskUserError")
        except AskUserError as exc:
            assert exc.code == "BAD_ANSWER"

    def test_multi_select_returns_multiple_labels(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=["A", "C"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question",
            _questions(
                options=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
                multi_select=True,
            ),
            _ctx(ask_user=ask),
        )
        assert not result.is_error
        payload = json.loads(result.text)
        assert payload["answers"][0]["selected"] == ["A", "C"]

    def test_multi_select_rejected_on_single_select_question(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=["Yes", "No"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question", _questions(), _ctx(ask_user=ask))
        assert result.is_error
        assert _code(result) == "BAD_ANSWER"
