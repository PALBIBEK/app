"""Compaction as pi's: what the summarizer reads, what the model reads after it."""

from __future__ import annotations

from typing import Any

from supervisor.seams.compaction.core import (
    SUMMARY_MARKER,
    SUMMARY_PREFIX,
    SUMMARY_SUFFIX,
    build_summary_request,
    extract_previous_summary,
    is_summary,
    serialize_conversation,
    summarize_span,
)


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"id": f"c-{name}", "name": name, "arguments": args}


def test_the_transcript_reads_as_pis_serialization() -> None:
    span = [
        {"role": "user", "content": [{"type": "text", "text": "fix the bug"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Looking."}],
            "tool_calls": [_call("read", path="a.py", offset=3)],
        },
        {"role": "tool", "tool_call_id": "c-read", "content": "x" * 2500},
        {"role": "assistant", "content": [{"type": "reasoning", "text": "hidden thoughts"}, {"type": "text", "text": "Done."}]},
        {"role": "tool", "tool_call_id": "c-empty", "content": ""},
    ]
    text = serialize_conversation(span)
    assert text.split("\n\n")[:3] == [
        "[User]: fix the bug",
        "[Assistant]: Looking.",
        '[Assistant tool calls]: read(path="a.py", offset=3)',
    ]
    assert f"[Tool result]: {'x' * 2000}\n\n[... 500 more characters truncated]" in text  # cut at 2000
    assert "hidden thoughts" not in text  # thinking is left out
    assert text.endswith("[Assistant]: Done.")  # an empty result adds nothing


def test_a_summary_lists_the_files_its_span_read_and_changed_and_carries_them_on() -> None:
    span = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                _call("read", path="src/a.py"),
                _call("read", path="src/b.py"),
                {"id": "w", "function": {"name": "edit", "arguments": '{"path": "src/b.py", "edits": []}'}},
                _call("write", file_path="NEW.md", content="x"),
            ],
        },
    ]
    first = summarize_span(span, summarizer=lambda messages, previous: "## Goal\nfix it")
    assert first == (
        f"{SUMMARY_PREFIX}## Goal\nfix it\n\n<read-files>\nsrc/a.py\n</read-files>\n\n"
        f"<modified-files>\nNEW.md\nsrc/b.py\n</modified-files>{SUMMARY_SUFFIX}"
    )
    assert is_summary(first)
    previous = extract_previous_summary([{"role": "user", "content": first + "\n\n[a note the run added]"}])
    assert previous is not None and previous.startswith("## Goal") and "[a note" not in previous

    seen: list[str | None] = []

    def summarizer(messages: list[dict[str, Any]], prior: str | None) -> str:
        seen.append(prior)
        return "## Goal\nfix it, then test"

    later = [{"role": "assistant", "content": "", "tool_calls": [_call("read", path="tests/t.py")]}]
    second = summarize_span(later, previous_summary=previous, summarizer=summarizer)
    assert "<read-files>\nsrc/a.py\ntests/t.py\n</read-files>" in second  # carried, and the new one
    assert "<modified-files>\nNEW.md\nsrc/b.py\n</modified-files>" in second
    request = build_summary_request(later, previous)[1]["content"]
    assert "<previous-summary>\n## Goal\nfix it\n</previous-summary>" in request  # lists not re-summarised
    assert "The messages above are NEW conversation messages" in request


def test_a_summary_journalled_in_the_older_shape_is_still_found() -> None:
    old = {"role": "user", "content": f"{SUMMARY_MARKER}\n## Goal\nold"}
    assert is_summary(old["content"])
    assert extract_previous_summary([old]) == "## Goal\nold"
