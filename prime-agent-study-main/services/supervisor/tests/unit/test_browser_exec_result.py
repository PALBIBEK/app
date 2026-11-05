"""What ``browser_exec`` makes of a finished call, with no browser: the command it sends
and the result it returns (the runner's report read back, its hints, long output cut)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from supervisor.genui import SHOW_UI
from supervisor.hands.browser import BROWSER_TOOLS, META_MARK, runtime_payload
from supervisor.tools import InvalidArguments, default_registry
from supervisor.tools.builtin.browser_exec import MAX_CODE_BYTES
from supervisor.tools.builtin.update_task import NAME as UPDATE_TASK
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import STDERR_SEPARATOR, CommandResult, HostWorkspace

pytestmark = pytest.mark.asyncio


class Answering(HostWorkspace):
    """A workspace whose commands answer ``result`` (files are real, under ``root``)."""

    def __init__(self, root: Path, result: CommandResult) -> None:
        super().__init__(root)
        self.result = result
        self.sent: list[tuple[str, Mapping[str, str]]] = []

    async def run(self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None, stop: Any = None) -> CommandResult:
        self.sent.append((command, dict(env or {})))
        return self.result


def _report(**fields: Any) -> str:
    return META_MARK + json.dumps(fields) + "\n"


async def _call(tmp_path: Path, result: CommandResult, **args: Any) -> tuple[dict[str, Any], Answering]:
    workspace = Answering(tmp_path, result)
    ctx = ToolContext(bound_workspace=workspace, timeout_s=30, env={"FROM_SESSION": "1"})
    payload = await default_registry()["browser_exec"].run({"code": "print(1)", **args}, ctx)
    return dict(payload), workspace


async def test_the_runner_and_the_code_travel_in_one_command(tmp_path: Path) -> None:
    _, workspace = await _call(tmp_path, CommandResult(0, "1\n", _report(started=True)), session="r7")

    ((command, env),) = workspace.sent
    folder, sources = runtime_payload()
    assert folder in command and all(source in command for source in sources.values()), "the runtime is not shipped with the call"
    assert len(command.encode()) < 128 * 1024
    assert env["FROM_SESSION"] == "1", "the session's own variables were dropped"
    assert env["GITHI_BROWSER_NAME"] == "r7"
    assert env["BROWSER_WORKSPACE"] == ".githi/browser"


async def test_the_largest_code_still_fits_one_shell_argument(tmp_path: Path) -> None:
    """Linux caps one argument at 128 KiB; the packed runtime and code of the largest size
    allowed (incompressible, the worst case) must fit in it."""
    code = secrets.token_hex(MAX_CODE_BYTES // 2)  # as random as text gets
    workspace = Answering(tmp_path, CommandResult(0, "", _report()))
    await default_registry()["browser_exec"].run({"code": code}, ToolContext(bound_workspace=workspace, timeout_s=30))

    ((command, _),) = workspace.sent
    assert len(command.encode()) < 128 * 1024, f"{len(command.encode())} bytes"


async def test_the_report_line_becomes_fields_and_the_rest_of_stderr_stays(tmp_path: Path) -> None:
    seen = [{"kind": "dialog", "tab": "T1", "type": "alert", "message": "Saved", "answer": "dismiss"}]
    stderr = "a warning from the code\n" + _report(started=True, url="https://shop.test/", error=None, events=seen)

    payload, _ = await _call(tmp_path, CommandResult(0, "1\n", stderr))

    assert payload["output"] == "1"
    assert payload["url"] == "https://shop.test/"
    assert payload["browser_started"] is True
    assert payload["stderr"] == "a warning from the code"
    assert payload["events"] == seen
    assert "error" not in payload


async def test_a_sandbox_without_a_browser_runtime_is_named(tmp_path: Path) -> None:
    missing = "ModuleNotFoundError: No module named 'playwright'"

    payload, _ = await _call(tmp_path, CommandResult(1, "", missing))

    assert "no browser runtime" in payload["hint"]


async def test_long_output_is_cut_and_kept_whole_in_the_workspace(tmp_path: Path) -> None:
    lines = "\n".join(f"item {i}" for i in range(5000))

    payload, _ = await _call(tmp_path, CommandResult(0, lines, _report(started=False)))

    assert payload["truncation"]["total_chars"] == len(lines)
    assert payload["output"].endswith("item 4999")
    assert (tmp_path / payload["full_output"]).read_text() == lines


async def test_output_the_workspace_cut_is_reported_and_kept_whole(tmp_path: Path) -> None:
    """The workspace keeps 12k characters of a stream (its start and end); the cut is said
    and the whole stdout, without the stderr kept beside it, goes to the workspace."""
    whole = "x" * 30_000
    shown = "x" * 6000 + "\n[... 18000 characters cut from the middle ...]\n" + "x" * 6000
    full = whole.encode() + STDERR_SEPARATOR + _report(started=False).encode()
    payload, _ = await _call(tmp_path, CommandResult(0, shown, _report(started=False), truncated=True, full=full))

    assert payload["truncation"]["total_chars"] == 30_000
    assert (tmp_path / payload["full_output"]).read_text() == whole


async def test_a_timeout_says_the_browser_kept_its_state(tmp_path: Path) -> None:
    payload, _ = await _call(tmp_path, CommandResult(-1, "", "", timed_out=True), timeout=5)

    assert payload["timed_out"] is True and payload["timeout_s"] == 5
    assert "keeps its state" in payload["hint"]


@pytest.mark.parametrize("code", ["", "   \n", "x" * (MAX_CODE_BYTES + 1)])
async def test_empty_or_oversized_code_is_refused_before_anything_runs(tmp_path: Path, code: str) -> None:
    workspace = Answering(tmp_path, CommandResult(0, "", ""))
    ctx = ToolContext(bound_workspace=workspace, timeout_s=30)

    with pytest.raises(InvalidArguments):
        await default_registry()["browser_exec"].run({"code": code}, ctx)
    assert not workspace.sent


@pytest.mark.parametrize("name", sorted(BROWSER_TOOLS | {SHOW_UI, UPDATE_TASK}))
async def test_the_shipped_tool_set_offers_the_browser_tools_as_implemented(name: str) -> None:
    """The catalog's copy of each tool (what sessions pin) is the implementation's own
    definition: a changed description or schema needs the tool set regenerated."""
    shipped = json.loads((Path(__file__).resolve().parents[4] / "harness/tool_sets/githi-browser-tools.json").read_text())
    definition = {k: v for k, v in shipped["definitions"][name].items() if k != "digest"}

    assert definition == default_registry()[name].definition()


async def test_the_browser_tool_set_is_the_coding_tools_the_browser_tools_and_reports() -> None:
    """A tool added to the coding tools (and to the harness's recovery list) belongs in the
    browser tool set too, or its composition is skipped at boot; a browser agent also shows
    what it found as a report (``show_ui``)."""
    sets = Path(__file__).resolve().parents[4] / "harness/tool_sets"
    coding = json.loads((sets / "githi-coding-tools.json").read_text())["definitions"]
    browser = json.loads((sets / "githi-browser-tools.json").read_text())["definitions"]

    assert set(browser) == set(coding) | BROWSER_TOOLS | {SHOW_UI, UPDATE_TASK}
    assert all(browser[name] == coding[name] for name in coding), "a coding tool's definition drifted"


async def test_the_shipped_browser_agent_reaches_the_web_and_is_told_how_to_browse() -> None:
    """Its sandbox has a Chromium and the open web (without a network the browser reaches
    no site), and its prompt is the browser pack's."""
    from supervisor.catalog.core.types import NetworkMode
    from supervisor.catalog.services.publish_checks import _check_network
    from supervisor.catalog.storage.converters import (
        composition_from_dict,
        prompt_pack_from_dict,
        sandbox_profile_from_dict,
    )

    harness = Path(__file__).resolve().parents[4] / "harness"
    read = lambda kind, name: json.loads((harness / kind / f"{name}.json").read_text())
    agent = composition_from_dict(read("compositions", "githi-browser-v1"))
    profile = sandbox_profile_from_dict(read("sandbox_profiles", agent.sandbox_profile.id))
    prompt = prompt_pack_from_dict(read("prompt_packs", agent.prompt_pack.id))

    assert profile.network is not None and profile.network.mode is NetworkMode.OPEN
    assert profile.browser == "sandbox-chromium" and "browser" in (profile.image_digest or "")
    _check_network(profile.name, profile)  # what publishing it checks
    text = " ".join(s.text for s in prompt.sections)
    assert prompt.persona == "browser" and "browser_vault" in text and "snapshot()" in text
