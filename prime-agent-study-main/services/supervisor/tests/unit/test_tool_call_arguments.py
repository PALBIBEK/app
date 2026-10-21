"""Tool-call arguments that are not a JSON object are marked, never emptied.

Run with ``{}`` a tool fails with a misleading "missing field", or runs on its
defaults; the loop refuses a marked call and tells the model to resend it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.models.messages import _tool_call
from supervisor.models.transports.base import parse_partial_json
from supervisor.seams.core.types import INVALID_ARGUMENTS_KEY


@pytest.mark.parametrize("raw", ['{"path": "."', "not json", "[1, 2]", '"a string"'])
def test_every_parser_marks_arguments_that_are_not_a_json_object(raw: str) -> None:
    streamed = parse_partial_json(raw)
    wire = _tool_call({"id": "c1", "function": {"name": "ls", "arguments": raw}}).arguments
    completion = SimpleNamespace(
        message=SimpleNamespace(tool_calls=[{"id": "c1", "function": {"name": "ls", "arguments": raw}}])
    )
    (extracted,) = AgentLoop.extract_tool_calls(completion)
    for args in (streamed, wire, extracted["arguments"]):
        assert set(args) == {INVALID_ARGUMENTS_KEY}, args


@pytest.mark.parametrize(("raw", "expected"), [("", {}), ("   ", {}), ('{"path": "."}', {"path": "."})])
def test_empty_and_valid_arguments_parse_as_before(raw: str, expected: dict) -> None:
    assert parse_partial_json(raw) == expected
    assert _tool_call({"id": "c1", "function": {"name": "ls", "arguments": raw}}).arguments == expected
