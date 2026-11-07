from __future__ import annotations

import asyncio
import base64
import json
import shlex
from typing import Any, Final

from pydantic import Field

from supervisor.hands.browser import (
    META_MARK,
    UNPACK,
    WORKSPACE_DIR,
    pack,
    run_runtime,
    runner_env,
)
from supervisor.hands.browser.url_policy import code_verdict
from supervisor.seams.core.types import RESULT_IMAGES
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.spill import spill
from supervisor.tools.truncate import cut_middle, truncate_tail
from supervisor.tools.workspace import STDERR_SEPARATOR

MAX_CODE_BYTES: Final = 48 * 1024
"""The code travels packed (compressed, then base64) inside the one shell argument a
command is, which Linux caps at 128 KiB (the runtime is stored apart: ``run_runtime``)."""
MAX_STDERR_CHARS: Final = 4000
REMOTE_ENV: Final = "GITHI_BROWSER_CDP"
"""Set when the session's browser is hosted (``supervisor.hands.browser.providers``)."""
MAX_SHOWN_BYTES: Final = 384 * 1024
"""The largest screenshot a result shows (a viewport's JPEG is a tenth of it); a larger
one, a long page's whole length, stays a file in the workspace."""
_GRACE_S: Final = 2.0
"""Kept back from the call's budget, as ``bash`` does, so a slow call is cut by its own
timeout (and returns what it printed) before the tool call times out."""
_NO_RUNTIME: Final = (
    "No module named 'playwright'",
    "python3: not found",
    "python3: command not found",
    "/chrome-linux/chrome'",  # Playwright without its Chromium
)
"""What a sandbox without the browser runtime (Python, Playwright, Chromium) reports."""


class BrowserExecArgs(ToolArgs):
    code: str
    """Python to run against the browser. Use print(...) for anything you need back"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """A named browser of its own (its own tabs, cookies and profile) for work that must not share the default one; reuse the name on every related call"""

    timeout: int | None = Field(default=None, ge=1)
    """Seconds the code may run (optional; the call's budget otherwise)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def browser_exec(args: BrowserExecArgs, ctx: ToolContext) -> dict[str, Any]:
    """Drive a real browser by running Python in the session's sandbox, for JavaScript pages, logins, forms and clicks (for plain reading, prefer fetching the page). Start code with a one-line comment naming the step in plain words (e.g. `# Searching the catalogue for paper towels`). Batch a whole sub-procedure (navigate, wait, extract, act) into one call, and print only what you need: stdout is the result. STATE: the browser (its tabs, cookies and current page) persists across calls; Python variables do not (each call is a fresh interpreter). The folder $BROWSER_WORKSPACE (`.githi/browser` in the workspace, also `WORKSPACE`) persists: for many items, append each batch to a JSON or CSV file there and aggregate it in code, counting against what was asked; functions saved in its agent_helpers.py are available in every call. IN SCOPE: `page`, `context`, `browser` are Playwright's sync API (page.goto, page.locator(...).click()/fill(), page.get_by_role(...), page.wait_for_selector, page.evaluate; page.wait_for_timeout(ms) ends early once the page is quiet). To see a page, print(snapshot()): its elements with [IDs] and the structure around them (snapshot(contains="text") narrows it to the matches and their groups); act(("click", "0-42"), ("fill", "0-7", "Ada"), ("select", "0-9", "Tech"), ("check", "0-3"), ("press", "0-7", "Enter")) acts by those IDs and settles; take a new snapshot after a page changes (old IDs are refused); locate("0-42") is that element as a Playwright locator, for what act() does not do (select_option with several labels, input_value). settle() waits for what an action started (navigation, requests, a changing DOM). Other helpers: new_tab(url) opens a tab and makes it `page`, goto_url(url), wait_for_load(), page_info() (url, title, viewport, scroll), js(expr) evaluates JavaScript and returns its value, fill_input(selector, text), click_at_xy(x, y), capture_screenshot() saves a PNG in the workspace and prints its path, show_screenshot() (full=True for the whole page) attaches a screenshot to this result for you to see when layout, images or canvas matter more than text, cdp('Domain.method', **params) sends raw CDP (cdp('Accessibility.getFullAXTree')['nodes'] lists every element: filter it in Python before printing), list_tabs(), switch_tab(id), ensure_real_tab(), answer_dialogs("accept") (or "dismiss") before an action that opens a native alert/confirm/prompt, wait_for_network_idle(), browser_events(), frames() (the page's frames with their frame_id; an iframe of another site is reached with browser_cdp(frame_id=...)). A dialog nothing answered is held: the page waits on it, the call ends with it in pending_dialogs, and browser_dialog answers it. Each result lists what the tabs reported since the previous call: dialogs with their answers, console errors, page errors (a repeat once, with times). Actions wait 5 s for their element (pass timeout= for a slower one). Login walls and checkouts: never guess credentials or card details; browser_vault fills what the user saved (logins with their 2FA codes, cards, addresses: list first), otherwise ask the user.

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
    env = {**ctx.env, **runner_env(args.session)}  # ctx.env: how to reach the session's browser
    verdict = await asyncio.to_thread(code_verdict, args.code, hosted=REMOTE_ENV in env)  # it resolves names
    if verdict.refused:
        return {"exit_code": None, "output": "", "error": verdict.refused, "blocked_by_policy": True}
    local = None
    if verdict.local:  # Hermes's hybrid routing: a private address goes to the sandbox's own browser
        local = f"{args.session or 'default'}-local"
        env = {k: v for k, v in env.items() if k != REMOTE_ENV} | runner_env(local)

    result = await run_runtime(ctx.workspace, _command(code), timeout_s=timeout, env=env, stop=ctx.stop)
    ctx.mutated()  # the code may write files (its workspace folder, screenshots)
    payload = await _payload(ctx, args, result, timeout)
    if local is not None:
        payload["local_browser"] = (
            f"this call reaches a private address, which the hosted browser must not: it ran on the sandbox's own browser, "
            f"session {local!r} (pass session={local!r} to go on there)"
        )
    return payload


def _command(code: bytes) -> str:
    """The runner, with ``code`` on its stdin (``run_runtime`` stores the runtime first when
    the sandbox has none of this version)."""
    return f'python3 -c {shlex.quote(UNPACK)} {pack(code)} | exec python3 "$d/runner.py"'


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
    if meta.get("events"):
        payload["events"] = meta["events"]  # since the previous call: dialogs (and their answers), console and page errors
    if meta.get("pending_dialogs"):
        payload["pending_dialogs"] = meta["pending_dialogs"]  # held for browser_dialog: the page waits on them
    if meta.get("blocked"):
        payload["blocked_by_dialog"] = True
    if meta.get("url"):
        payload["url"] = meta["url"]
    if meta.get("shown"):
        payload.update(await _screenshots(ctx, meta["shown"]))
    if meta.get("started"):
        payload["browser_started"] = True  # a fresh browser: earlier tabs and logins are gone
    if meta.get("fallback"):  # the hosted browser failed: the sandbox's own served the call
        payload["fallback_from_cloud"] = meta["fallback"]
    if args.session:
        payload["session"] = args.session
    payload["workspace"] = WORKSPACE_DIR
    if any(marker in stderr or marker in str(meta.get("error") or "") for marker in _NO_RUNTIME):
        payload["hint"] = "This sandbox has no browser runtime: the session needs a sandbox image with Python, Playwright and Chromium."
    if result.stopped:
        payload["stopped"] = True
    if result.timed_out:
        payload["timed_out"] = True
        payload["timeout_s"] = timeout
        payload["hint"] = "The code ran out of time; the browser keeps its state. Split the work into calls that append to workspace files."
    return payload


async def _screenshots(ctx: ToolContext, shown: list[str]) -> dict[str, Any]:
    """The screenshots the code asked to show: their paths, and those small enough as
    images (``RESULT_IMAGES``) for the model to see."""
    paths = [f"{WORKSPACE_DIR}/{name}" for name in shown]
    images, too_large = [], []
    for path in paths:
        data = await ctx.workspace.read_bytes(path)
        if len(data) > MAX_SHOWN_BYTES:
            too_large.append(f"{path} ({len(data) // 1024} KiB, over {MAX_SHOWN_BYTES // 1024} KiB: show the viewport)")
        else:
            images.append({"mime_type": "image/jpeg", "data": base64.b64encode(data).decode("ascii")})
    return {"screenshots": paths, **({"not_shown": too_large} if too_large else {}), **({RESULT_IMAGES: images} if images else {})}


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
