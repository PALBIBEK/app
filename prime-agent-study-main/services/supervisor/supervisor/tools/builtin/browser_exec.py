from __future__ import annotations

import base64
import json
import shlex
from typing import Any, Final

from pydantic import Field

from supervisor.hands.browser import (
    META_MARK,
    RUNNER_DIR,
    BrowserProvider,
    SandboxChromium,
    runner_payload,
)
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.spill import spill
from supervisor.tools.truncate import cut_middle, truncate_tail
from supervisor.tools.workspace import STDERR_SEPARATOR

WORKSPACE_DIR: Final = ".githi/browser"
"""The browser's folder in the session's workspace: saved with the session, so batch
files and the agent's own helpers survive a lost sandbox (the open tabs do not)."""
MAX_CODE_BYTES: Final = 64 * 1024
"""The code travels in base64 inside the one shell argument a command is, which Linux
caps at 128 KiB, beside the runner's source."""
MAX_STDERR_CHARS: Final = 4000
_GRACE_S: Final = 2.0
"""Kept back from the call's budget, as ``bash`` does, so a slow call is cut by its own
timeout (and returns what it printed) before the tool call times out."""
_NO_BROWSER_EXIT: Final = 2
"""The runner's exit status when it could not reach a browser."""
_NO_RUNTIME: Final = ("No module named 'playwright'", "python3: not found", "python3: command not found")

PROVIDER: BrowserProvider = SandboxChromium()
"""Where calls find the browser (``supervisor.hands.browser``'s seam)."""


class BrowserExecArgs(ToolArgs):
    code: str
    """Python to run against the browser. Use print(...) for anything you need back"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """A named browser of its own (its own tabs, cookies and profile) for work that must not share the default one; reuse the name on every related call"""

    timeout: int | None = Field(default=None, ge=1)
    """Seconds the code may run (optional; the call's budget otherwise)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def browser_exec(args: BrowserExecArgs, ctx: ToolContext) -> dict[str, Any]:
    """Drive a real browser by running Python in the session's sandbox, for JavaScript pages, logins, forms and clicks (for plain reading, prefer fetching the page). Start code with a one-line comment naming the step in plain words (e.g. `# Searching the catalogue for paper towels`). Batch a whole sub-procedure (navigate, wait, extract, act) into one call, and print only what you need: stdout is the result. STATE: the browser (its tabs, cookies and current page) persists across calls; Python variables do not (each call is a fresh interpreter). The folder $BROWSER_WORKSPACE (`.githi/browser` in the workspace, also `WORKSPACE`) persists: for many items, append each batch to a JSON or CSV file there and aggregate it in code, counting against what was asked; functions saved in its agent_helpers.py are available in every call. IN SCOPE: `page`, `context`, `browser` are Playwright's sync API (page.goto, page.locator(...).click()/fill(), page.get_by_role(...), page.wait_for_selector, page.evaluate); helpers: new_tab(url) opens a tab and makes it `page`, goto_url(url), wait_for_load(), page_info() (url, title, viewport, scroll), js(expr) evaluates JavaScript and returns its value, fill_input(selector, text), click_at_xy(x, y), capture_screenshot() saves a PNG in the workspace and prints its path, cdp('Domain.method', **params) sends raw CDP (cdp('Accessibility.getFullAXTree')['nodes'] lists every element: filter it in Python before printing), list_tabs(), switch_tab(id), ensure_real_tab(), answer_dialogs("accept") before an action that opens a native alert/confirm/prompt (dialogs are dismissed otherwise, and every one is listed in the result). Actions wait 5 s for their element (pass timeout= for a slower one). Login walls: never guess credentials; ask the user.

    Code mode, as Hermes's ``browser_exec`` and Stagehand's ``run``: the runner
    (``supervisor.hands.browser.runner``) is shipped into the sandbox and runs the code
    there with the session's browser connected, so a sequence of steps costs one model
    turn and no network round trip per step, and only what the code prints comes back
    (cut as ``bash`` cuts its output, the whole of it kept in the workspace). A browser
    the runner had to start afresh (the sandbox was replaced, say) is reported, so the
    model knows its tabs and logins are gone. A call can do anything, so it is
    non-atomic: a crash mid-call is not retried.
    """
    if not args.code.strip():
        raise InvalidArguments('code must not be empty: pass Python such as new_tab("https://example.com") then print(page_info())')
    code = args.code.encode("utf-8")
    if len(code) > MAX_CODE_BYTES:
        raise InvalidArguments(f"code is {len(code)} bytes; at most {MAX_CODE_BYTES}: split the work into several calls")
    ceiling = max(ctx.timeout_s - _GRACE_S, 1.0)
    timeout = min(float(args.timeout), ceiling) if args.timeout else ceiling

    result = await ctx.workspace.run(
        _command(code),
        timeout_s=timeout,
        env={**ctx.env, **PROVIDER.env(), **_runner_env(args.session)},
        stop=ctx.stop,
    )
    ctx.mutated()  # the code may write files (its workspace folder, screenshots)
    return await _payload(ctx, args, result, timeout)


def _command(code: bytes) -> str:
    """Store the runner in the sandbox once per version, then run it with ``code`` on stdin."""
    path, source = runner_payload()
    decode = "import base64,sys;sys.stdout.buffer.write(base64.b64decode(sys.argv[1]))"
    return (
        f"r={shlex.quote(path)}\n"
        f'[ -f "$r" ] || {{ mkdir -p {shlex.quote(RUNNER_DIR)} && '
        f'python3 -c {shlex.quote(decode)} {source} > "$r.$$" && mv "$r.$$" "$r"; }}\n'
        f"python3 -c {shlex.quote(decode)} {base64.b64encode(code).decode('ascii')} | exec python3 \"$r\""
    )


def _runner_env(session: str | None) -> dict[str, str]:
    return {"BROWSER_WORKSPACE": WORKSPACE_DIR, **({"GITHI_BROWSER_NAME": session} if session else {})}


async def _payload(ctx: ToolContext, args: BrowserExecArgs, result: Any, timeout: float) -> dict[str, Any]:
    """The call's result as the model reads it: what the code printed, then the rest."""
    stderr, meta = _split_meta(result.stderr)
    shown = truncate_tail(result.stdout.rstrip("\n"))
    payload: dict[str, Any] = {"exit_code": result.exit_code, "output": shown.content}
    if shown.truncated or result.truncated:
        # The workspace cuts a long stream in the middle; the whole of it is in ``full``.
        whole = _stdout_of(result.full) if result.truncated and result.full else result.stdout.encode("utf-8")
        payload["truncation"] = {"total_chars": len(whole.decode("utf-8", errors="replace"))}
        if kept := await spill(ctx, whole, kind="browser"):
            payload["full_output"] = kept
    if meta.get("error"):
        payload["error"] = meta["error"]
    if stderr:
        payload["stderr"] = cut_middle(stderr, MAX_STDERR_CHARS)
    if meta.get("dialogs"):
        payload["dialogs"] = meta["dialogs"]  # answered as the code asked (dismissed by default)
    if meta.get("url"):
        payload["url"] = meta["url"]
    if meta.get("started"):
        payload["browser_started"] = True  # a fresh browser: earlier tabs and logins are gone
    if args.session:
        payload["session"] = args.session
    payload["workspace"] = WORKSPACE_DIR
    if result.exit_code == _NO_BROWSER_EXIT or any(marker in stderr for marker in _NO_RUNTIME):
        payload["hint"] = "This sandbox has no browser runtime: the session needs a sandbox image with Python, Playwright and Chromium."
    if result.stopped:
        payload["stopped"] = True
    if result.timed_out:
        payload["timed_out"] = True
        payload["timeout_s"] = timeout
        payload["hint"] = "The code ran out of time; the browser keeps its state. Split the work into calls that append to workspace files."
    return payload


def _stdout_of(full: bytes) -> bytes:
    """A cut command's whole stdout, out of what the workspace keeps (stdout, then stderr)."""
    return full.split(STDERR_SEPARATOR, 1)[0]


def _split_meta(stderr: str) -> tuple[str, dict[str, Any]]:
    """The code's own stderr, and the runner's report line (the last one it wrote)."""
    meta: dict[str, Any] = {}
    kept: list[str] = []
    for line in stderr.split("\n"):  # not splitlines(): it breaks at the mark's \x1e too
        if line.startswith(META_MARK):
            try:
                meta = json.loads(line[len(META_MARK):])
            except ValueError:
                kept.append(line)
        else:
            kept.append(line)
    return "\n".join(kept).strip(), meta
