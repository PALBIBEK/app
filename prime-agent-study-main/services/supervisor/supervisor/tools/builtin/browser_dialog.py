from __future__ import annotations

import json
from typing import Any, Final, Literal

from pydantic import Field

from supervisor.hands.browser import DIALOG_ENV, run_runtime, runner_env
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

DIALOG_TIMEOUT_S: Final = 30.0


class BrowserDialogArgs(ToolArgs):
    action: Literal["accept", "dismiss"]
    """accept: OK (a prompt returns prompt_text; a beforeunload lets the page go); dismiss: Cancel (a prompt returns null)"""

    prompt_text: str | None = Field(default=None, max_length=1_000)
    """What a prompt() dialog answers (ignored for alert, confirm and beforeunload)"""

    dialog_id: str | None = Field(default=None, pattern=r"^d-\d{1,9}$")
    """Which dialog, as pending_dialogs names it: only when more than one is open"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """The named browser, as browser_exec's session (omit for the default one)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def browser_dialog(args: BrowserDialogArgs, ctx: ToolContext) -> dict[str, Any]:
    """Accept or dismiss a native JavaScript dialog (alert, confirm, prompt, beforeunload) the browser holds: one browser_exec reported in pending_dialogs, which the page waits on. Pass prompt_text to answer a prompt(); with several open, the error lists their ids for dialog_id. To have a step's dialogs answered as they open instead, call answer_dialogs("accept") in browser_exec before it.

    Hermes's ``browser_dialog``, against the CDP supervisor of the sandbox's browser
    daemon (``supervisor.hands.browser.cdp_supervisor``), which caught the dialog natively
    or through its dialog bridge (so it works on a hosted browser that dismisses native
    dialogs itself). The runtime's ``dialog`` answers it and starts no browser.
    """
    request = {"action": args.action, "text": args.prompt_text, "dialog_id": args.dialog_id}
    result = await run_runtime(
        ctx.workspace,
        'exec python3 "$d/runner.py" dialog',
        timeout_s=min(DIALOG_TIMEOUT_S, ctx.timeout_s),
        env={
            **ctx.env,
            **runner_env(args.session),
            DIALOG_ENV: json.dumps({k: v for k, v in request.items() if v is not None}),
        },
        stop=ctx.stop,
    )
    try:
        answer: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {
            "success": False,
            "error": "the browser did not answer",
            "exit_code": result.exit_code,
        }
    if answer.get("ok"):
        return {"success": True, "action": args.action, "dialog": answer.get("dialog", {})}
    return {"success": False, "error": answer.get("error", "unknown error")}
