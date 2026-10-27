"""githi's harness: what the model reads of tool results, and the system prompt it is sent."""

from __future__ import annotations

from supervisor.agent.runtimes.githi.prompt import system_prompt, tool_line
from supervisor.agent.runtimes.githi.render import bound, tool_result_text


def test_a_command_reads_like_a_terminal_and_a_failure_says_so() -> None:
    ok = {
        "ok": True,
        "payload": {"exit_code": 0, "stdout": "a\nb\n", "stderr": ""},
        "error": None,
        "code": None,
    }
    assert tool_result_text(ok) == "a\nb"
    failed = {"ok": True, "payload": {"exit_code": 3, "stdout": "checking\n", "stderr": "boom\n"}}
    assert tool_result_text(failed) == "checking\n[stderr]\nboom\n[exit code 3: the command failed]"
    silent = {"ok": True, "payload": {"exit_code": 0, "stdout": "", "stderr": ""}}
    assert tool_result_text(silent) == "[no output; exit code 0]"
    killed = {
        "ok": True,
        "payload": {
            "exit_code": -9,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "notice": "killed after 5s",
        },
    }
    assert tool_result_text(killed) == "[killed after 5s]\n[the command timed out and was killed]"


def test_text_stays_text_and_structure_reads_as_fields() -> None:
    assert tool_result_text(
        {"ok": True, "payload": '     1\tdef f():\n     2\t    return "x"'}
    ) == ('     1\tdef f():\n     2\t    return "x"')
    edit = {
        "ok": True,
        "payload": {"path": "a.py", "replacements": 1, "diff": "--- a/a.py\n+++ b/a.py\n"},
    }
    assert tool_result_text(edit) == "path: a.py\nreplacements: 1\ndiff:\n--- a/a.py\n+++ b/a.py"
    assert tool_result_text({"ok": True, "payload": None}) == "[done]"


def test_errors_are_named_with_their_code_and_old_shapes_still_read() -> None:
    assert (
        tool_result_text(
            {"ok": False, "payload": None, "error": "denied by approval", "code": "APPROVAL_DENIED"}
        )
        == "Error (APPROVAL_DENIED): denied by approval"
    )
    lost = {"error": "outcome unknown: worker lost", "code": "TOOL_OUTCOME_UNKNOWN"}  # no "ok"
    assert tool_result_text(lost) == "Error (TOOL_OUTCOME_UNKNOWN): outcome unknown: worker lost"
    assert tool_result_text({"text": "already rendered", "ok": True}) == "already rendered"


def test_long_results_keep_head_and_tail() -> None:
    text = "\n".join(f"line {i}" for i in range(5000))
    out = bound(text)
    assert out.startswith("line 0\n") and out.endswith("line 4999")
    assert "[... 3000 lines cut ...]" in out
    wide = bound("x" * 200, chars=100)
    assert wide.startswith("x" * 50) and "[... 100 characters cut ...]" in wide


def test_the_prompt_follows_pi_preamble_tools_rules_cwd() -> None:
    tools = [
        {
            "type": "function",
            "function": {"name": "read", "description": "Read a file. More detail here."},
        },
        {
            "type": "function",
            "function": {"name": "bash", "description": "Run a shell command.\nMore."},
        },
        {
            "type": "function",
            "function": {"name": "remember", "description": "Save a fact. Then more."},
        },
    ]
    assert tool_line(tools[0]) == "- read: Read file contents"
    assert tool_line(tools[2]) == "- remember: Save a fact"
    prompt = system_prompt("You are a coding agent.", tools)
    assert prompt == (
        "You are a coding agent.\n\n"
        "<tools>\n- read: Read file contents\n- bash: Execute bash commands (ls, grep, find, etc.)\n"
        "- remember: Save a fact\n\nIn addition to the tools above, you may have access to other custom "
        "tools depending on the project.\n</tools>\n\n"
        "<rules>\n- Use bash for file operations like ls, rg, find\n"
        "- Use read to examine files instead of cat or sed.\n- Be concise in your responses\n"
        "- Show file paths clearly when working with files\n</rules>\n\n<cwd>\n/workspace\n</cwd>"
    )


def test_the_prompt_is_stable_so_the_provider_cache_hits() -> None:
    tools = [
        {"type": "function", "function": {"name": "edit", "description": "Edit."}},
        {"type": "function", "function": {"name": "grep", "description": "Search."}},
    ]
    first, again = system_prompt("Pinned.", tools), system_prompt("Pinned.", list(tools))
    assert first == again
    assert "Use bash for file operations" not in first  # grep is offered
    assert "Use edit for precise changes (old_text must match exactly)" in first
    assert (
        system_prompt("", [])
        == "<rules>\n- Be concise in your responses\n- Show file paths clearly when working with files\n</rules>\n\n<cwd>\n/workspace\n</cwd>"
    )


def test_a_summary_request_reads_the_span_as_a_transcript() -> None:
    from supervisor.seams.compaction.core import build_summary_request

    span = [
        {"role": "user", "content": [{"type": "text", "text": "fix the bug"}]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "read", "arguments": {"path": "a.py"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
    ]
    request = build_summary_request(span)
    # Only system + one user message: no orphaned tool messages for a provider to refuse.
    assert [m["role"] for m in request] == ["system", "user"]
    text = request[1]["content"]
    assert "[User]: fix the bug" in text
    assert '[Assistant tool calls]: read({"path": "a.py"})' in text
    assert "[Tool result]: print(1)" in text


def test_tool_results_are_read_back_in_the_order_the_calls_were_made() -> None:
    from supervisor.temporal.steps import _in_call_order

    asked = {
        "role": "assistant",
        "content": [],
        "tool_calls": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
    }
    done = [{"role": "tool", "tool_call_id": i, "content": []} for i in ("c", "a", "b")]
    later = {"role": "user", "content": []}
    out = _in_call_order([asked, *done, later])
    assert [m.get("tool_call_id") for m in out[1:4]] == ["a", "b", "c"]
    assert out[0] is asked and out[-1] is later
