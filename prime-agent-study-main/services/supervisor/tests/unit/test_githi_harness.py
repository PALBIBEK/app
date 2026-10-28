"""githi's harness: what the model reads of tool results, and the system prompt it is sent."""

from __future__ import annotations

from supervisor.agent.runtimes.githi.prompt import system_prompt, tool_line
from supervisor.agent.runtimes.githi.render import bound, tool_result_failed, tool_result_text


def test_a_command_reads_as_pis_bash_result() -> None:
    def shell(**payload: object) -> dict[str, object]:
        return {"ok": True, "payload": {"exit_code": 0, "output": "", **payload}}

    assert tool_result_text(shell(output="a\nb\n")) == "a\nb\n"
    assert not tool_result_failed(shell(output="a\n"))
    assert tool_result_text(shell()) == "(no output)"
    failed = shell(exit_code=3, output="checking\nboom\n")
    assert tool_result_text(failed) == "checking\nboom\n\n\nCommand exited with code 3"
    assert tool_result_failed(failed)  # sent to the provider flagged, as pi's isError
    assert tool_result_text(shell(exit_code=1)) == "(no output)\n\nCommand exited with code 1"
    killed = shell(exit_code=-9, output="started\n", timed_out=True, timeout_s=5.0)
    assert tool_result_text(killed) == "started\n\n\nCommand timed out after 5 seconds"
    assert tool_result_text(shell(exit_code=-9, timed_out=True, timeout_s=2.5)) == (
        "Command timed out after 2.5 seconds"
    )
    assert tool_result_failed(killed)
    cut = shell(
        output="1999\n2000",
        truncation={"by": "lines", "start": 2, "end": 2001, "total": 2001, "shown_bytes": 9, "partial": False},
        full_output=".githi/output/bash-1.log",
    )
    assert tool_result_text(cut) == (
        "1999\n2000\n\n[Showing lines 2-2001 of 2001. Full output: .githi/output/bash-1.log]"
    )
    by_bytes = shell(output="x", truncation={"by": "bytes", "start": 9, "end": 10, "total": 10, "partial": False},
                     full_output="f.log")
    assert tool_result_text(by_bytes).endswith("[Showing lines 9-10 of 10 (50.0KB limit). Full output: f.log]")
    partial = shell(output="xyz", truncation={"by": "bytes", "start": 1, "end": 1, "total": 1, "partial": True,
                                              "shown_bytes": 51200, "line_bytes": 2 * 1024 * 1024},
                    full_output="f.log")
    assert tool_result_text(partial).endswith(
        "[Showing last 50.0KB of line 1 (line is 2.0MB). Full output: f.log]"
    )


def test_a_command_journalled_in_the_older_shape_still_reads() -> None:
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


def test_write_and_edit_report_as_pis_do() -> None:
    edited = {"ok": True, "payload": {"path": "a.py", "replacements": 2, "diff": "--- a/a.py\n"}}
    assert tool_result_text(edited, "edit") == "Successfully replaced 2 block(s) in a.py."
    written = {"ok": True, "payload": {"path": "new/b.txt", "bytes": 3}}
    assert tool_result_text(written, "write") == "Successfully wrote to new/b.txt"
    assert tool_result_text({"ok": True, "payload": "x"}, "edit") == "x"  # not the payload it knows


def test_an_error_reads_as_its_message_and_old_shapes_still_read() -> None:
    """As pi's: a failed call's result is the error's message, flagged as an error to
    the provider; the code stays in the journal for traces."""
    denied = {"ok": False, "payload": None, "error": "denied by approval", "code": "APPROVAL_DENIED"}
    assert tool_result_text(denied) == "denied by approval"
    assert tool_result_failed(denied)
    lost = {"error": "outcome unknown: worker lost", "code": "TOOL_OUTCOME_UNKNOWN"}  # no "ok"
    assert tool_result_text(lost) == "outcome unknown: worker lost"
    assert tool_result_failed(lost)
    assert tool_result_text({"text": "already rendered", "ok": True}) == "already rendered"
    assert not tool_result_failed({"ok": True, "payload": "x"})


def test_long_results_keep_head_and_tail() -> None:
    text = "\n".join(f"line {i}" for i in range(5000))
    out = bound(text)
    assert out.startswith("line 0\n") and out.endswith("line 4999")
    assert "[... 2984 lines cut ...]" in out  # 2000 lines and room for a tool's own note
    page = "\n".join(["row"] * 2000) + "\n\n[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]"
    assert bound(page) == page  # a page a tool already cut is not cut again
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
        "<rules>\n- Your working directory is the project root: bash runs there, and file paths are "
        "relative to it (e.g. src/app.py)\n- Use bash for file operations like ls, rg, find\n"
        "- Use read to examine files instead of cat or sed.\n- Be concise in your responses\n"
        "- Show file paths clearly when working with files\n</rules>\n\n<cwd>\n.\n</cwd>"
    )


def test_the_prompt_is_stable_so_the_provider_cache_hits() -> None:
    tools = [
        {"type": "function", "function": {"name": "edit", "description": "Edit."}},
        {"type": "function", "function": {"name": "grep", "description": "Search."}},
    ]
    first, again = system_prompt("Pinned.", tools), system_prompt("Pinned.", list(tools))
    assert first == again
    assert "Use bash for file operations" not in first  # grep is offered
    assert "Use edit for precise changes (edits[].oldText must match exactly)" in first
    assert "- grep: Search file contents for patterns (respects .gitignore)" in first
    assert (
        system_prompt("", [])
        == "<rules>\n- Be concise in your responses\n- Show file paths clearly when working with files\n</rules>\n\n<cwd>\n.\n</cwd>"
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


def test_a_restore_notice_reaches_the_model_after_the_round_it_happened_in() -> None:
    """Between a reply's calls and their results a note would break the exchange: it is
    shown once the round's results are in."""
    from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, ToolStart, WorkspaceRestored
    from supervisor.temporal.steps import conversation

    def env(seq: int, payload: object) -> Envelope:
        return Envelope("s", seq, f"e{seq}", seq, "r1", None, payload)  # type: ignore[arg-type]

    call = {"type": "tool_call", "id": "c2", "name": "read", "arguments": {"path": "a"}}
    run = [
        env(0, MessageEnd(role="user", content=[{"type": "text", "text": "go"}])),
        env(1, MessageEnd(role="assistant", content=[call])),
        env(2, ToolStart("c2", "read", {"path": "a"})),
        env(3, WorkspaceRestored(hand="default", hand_id="h2", lost=["edit (c1)"], text="[rebuilt: redo edit (c1)]")),
        env(4, ToolEnd("c2", "read", {"ok": True, "payload": "x"}, is_error=False)),
    ]
    roles = [(m["role"], m.get("tool_call_id")) for m in conversation([], run)]
    assert roles == [("user", None), ("assistant", None), ("tool", "c2"), ("user", None)]
    assert conversation([], run)[-1]["content"][0]["text"] == "[rebuilt: redo edit (c1)]"
    later = [*run, env(5, MessageEnd(role="assistant", content=[{"type": "text", "text": "ok"}]))]
    assert [m["role"] for m in conversation([], later)][-2:] == ["user", "assistant"]


def test_a_tool_call_id_the_run_has_used_is_renamed_before_it_is_journalled() -> None:
    """Calls are journalled by id: a reused id ("call_0" each step) or none at all would
    be answered with an earlier call's result without running."""
    from supervisor.agent.runtimes.githi.classify import unique_call_ids

    parts = [
        {"type": "text", "text": "x"},
        {"type": "tool_call", "id": "call_0", "name": "ls", "arguments": {}},
        {"type": "tool_call", "id": "", "name": "ls", "arguments": {}},
        {"type": "tool_call", "id": "fresh", "name": "ls", "arguments": {}},
        {"type": "tool_call", "id": "fresh", "name": "ls", "arguments": {}},
    ]
    ids = [p.get("id") for p in unique_call_ids(parts, {"call_0"}, 2)][1:]
    assert ids == ["call_0~2.1", "call~2.2", "fresh", "fresh~2.4"]
    assert len(set(ids)) == len(ids)


def test_the_prompt_lists_pis_tools_first_and_carries_the_projects_instructions() -> None:
    def fn(name: str) -> dict[str, object]:
        return {"type": "function", "function": {"name": name, "description": f"{name} tool."}}

    prompt = system_prompt("Pinned.", [fn("memory"), fn("write"), fn("ls"), fn("read"), fn("bash"), fn("edit")],
                           project=[("AGENTS.md", "Run `make test` before you finish.\n")])
    listed = [line.split(":")[0][2:] for line in prompt.split("<tools>\n")[1].split("\n\n")[0].splitlines()]
    assert listed == ["read", "bash", "edit", "write", "ls", "memory"]  # pi's order, then the rest
    assert (
        "<project_context>\nProject-specific instructions and guidelines:\n\n"
        '<project_instructions path="AGENTS.md">\nRun `make test` before you finish.\n</project_instructions>\n'
        "</project_context>\n\n<cwd>"
    ) in prompt


def test_the_persona_label_stays_out_of_githis_prompt() -> None:
    from supervisor.catalog.core.types import PromptPack, PromptSection
    from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
    from supervisor.seams.core.types import PromptBindings

    pack = PromptPack(name="p", persona="coder", sections=(PromptSection(id="intro", text="You are an expert.", order=1),),
                      vars=(), semver="1.0.0", digest="", status="offered", protocol_version=1)  # type: ignore[call-arg]
    assembler = CatalogPromptAssembler(pack)
    assert assembler.assemble(PromptBindings(include_persona=False)).text == "You are an expert."
    assert assembler.assemble(PromptBindings()).text.startswith("coder\n\n")  # other runtimes unchanged
