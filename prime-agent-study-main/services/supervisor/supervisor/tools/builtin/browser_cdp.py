from __future__ import annotations

import json
from typing import Any, Final

from pydantic import Field

from supervisor.hands.browser import CDP_ENV, run_runtime, runner_env
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

CDP_DOCS_URL: Final = "https://chromedevtools.github.io/devtools-protocol/"
MAX_TIMEOUT_S: Final = 300.0
MAX_RESULT_CHARS: Final = 100_000
"""A result longer than this (a whole DOM, say) is cut: filter it in browser_exec instead."""


class BrowserCdpArgs(ToolArgs):
    method: str = Field(pattern=r"^[A-Z][A-Za-z]*\.[a-zA-Z]+$")
    """The CDP method, e.g. 'Target.getTargets', 'Runtime.evaluate', 'Network.getAllCookies'"""

    params: dict[str, Any] | None = None
    """The method's parameters as a JSON object (omit for none)"""

    target_id: str | None = Field(default=None, max_length=64)
    """A tab, by the targetId Target.getTargets (or list_tabs) gives: for page-level methods (Page.*, Runtime.*, DOM.*, Emulation.*)"""

    frame_id: str | None = Field(default=None, max_length=64)
    """An iframe of another site (out-of-process), by the frame_id frames() gives: for methods inside it, such as Runtime.evaluate"""

    timeout: float | None = Field(default=None, gt=0, le=MAX_TIMEOUT_S)
    """Seconds to wait for the answer (default 30)"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """The named browser, as browser_exec's session (omit for the default one)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def browser_cdp(args: BrowserCdpArgs, ctx: ToolContext) -> dict[str, Any]:
    """Send one raw Chrome DevTools Protocol command to the session's browser: the escape hatch for what browser_exec's helpers do not cover. Browser-level methods (Target.*, Browser.*, Storage.*): omit target_id and frame_id. Page-level methods: pass target_id. Inside an iframe of another site (Runtime.evaluate there, say): pass its frame_id from frames(); a same-site iframe is reached from its page instead (contentDocument in Runtime.evaluate). Method reference: https://chromedevtools.github.io/devtools-protocol/. Each call is one command: event subscriptions do not last between calls.

    Hermes's ``browser_cdp``, over the live connection of the sandbox's CDP supervisor
    (``supervisor.hands.browser.cdp_supervisor``): a ``frame_id`` goes through that
    iframe's child session, which is the only way into an out-of-process iframe (and, on a
    hosted browser, the one connection that outlives its signed URL); a ``target_id``
    through the tab's session. Results pass the vault's cleaning like every tool's.
    """
    if args.target_id and args.frame_id:
        raise InvalidArguments(
            "pass target_id or frame_id, not both: a frame_id already names its tab"
        )
    timeout = min(args.timeout or 30.0, MAX_TIMEOUT_S, max(ctx.timeout_s - 5.0, 1.0))
    request = {
        "method": args.method,
        "params": args.params or {},
        "target_id": args.target_id,
        "frame_id": args.frame_id,
        "timeout": timeout,
    }
    result = await run_runtime(
        ctx.workspace,
        'exec python3 "$d/runner.py" cdp',
        timeout_s=min(timeout + 10.0, ctx.timeout_s),
        env={
            **ctx.env,
            **runner_env(args.session),
            CDP_ENV: json.dumps({k: v for k, v in request.items() if v is not None}),
        },
        stop=ctx.stop,
    )
    try:
        answer: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {
            "success": False,
            "method": args.method,
            "error": "the browser did not answer",
            "exit_code": result.exit_code,
        }
    if not answer.get("ok"):
        return {
            "success": False,
            "method": args.method,
            "error": answer.get("error", "unknown error"),
            "cdp_docs": CDP_DOCS_URL,
        }
    payload = {"success": True, **{k: v for k, v in answer.items() if k != "ok"}}
    text = json.dumps(payload["result"], default=str)
    if len(text) > MAX_RESULT_CHARS:
        payload["result"] = text[:MAX_RESULT_CHARS]
        payload["truncated"] = {
            "total_chars": len(text),
            "hint": "filter the result in browser_exec (cdp(...) there) instead",
        }
    return payload
