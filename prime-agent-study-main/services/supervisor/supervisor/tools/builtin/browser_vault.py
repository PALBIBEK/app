from __future__ import annotations

import json
from typing import Any, Final, Literal

from pydantic import Field

from supervisor.hands.browser import FILL_ENV, runner_env, runtime_command
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments, UnsupportedCapability
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.vault.logins import InvalidLogin

FILL_TIMEOUT_S: Final = 30.0


class BrowserVaultArgs(ToolArgs):
    action: Literal["list", "fill"]
    """list: the sites with a saved login; fill: type one into the current tab"""

    login: str | None = None
    """fill: the site whose login to use, as list names it (e.g. https://shop.example.com)"""

    username: str | None = None
    """fill: the username field, by snapshot ID ("0-12") or CSS selector"""

    password: str | None = None
    """fill: the password field, by snapshot ID or CSS selector"""

    submit: str | None = None
    """fill: what to click after filling (optional), by snapshot ID or CSS selector"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """The named browser, as browser_exec's session (omit for the default one)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def browser_vault(args: BrowserVaultArgs, ctx: ToolContext) -> dict[str, Any]:
    """Use a login the user saved, without seeing it: list the sites that have one, or fill one into the browser's current tab (it must be on that site) by naming the username and password fields (snapshot IDs or CSS selectors) and, optionally, what to click to submit. The values are typed for you and never shown to you; never ask the user for a password the vault holds, and never type one yourself.

    Hermes's ``browser_vault``, on the tenant's own vault (``supervisor.vault.logins``):
    the supervisor reads the login when it fills, and the sandbox gets it only for the
    fill, in the runtime's ``fill`` (a process the model's code is not part of), which
    refuses a tab on another origin. Every tool result is cleaned of saved passwords
    before the model sees it, so code that reads the field back gets ``[redacted:login]``.
    """
    if ctx.logins is None:
        raise UnsupportedCapability("this deployment keeps no saved logins (it has no vault)")
    if args.action == "list":
        return {"logins": await ctx.logins.origins()}
    if not args.login or not (args.username or args.password):
        raise InvalidArguments("fill takes a login (as list names it) and a username and/or password field")
    try:
        login = await ctx.logins.get(args.login)
    except InvalidLogin as exc:
        raise InvalidArguments(str(exc)) from None
    if login is None:
        return {"error": f"no saved login for {args.login}", "logins": await ctx.logins.origins()}
    request = {
        "origin": login.origin,
        "targets": {k: v for k, v in (("username", args.username), ("password", args.password), ("submit", args.submit)) if v},
        "values": {"username": login.username, "password": login.password},
    }
    result = await ctx.workspace.run(
        runtime_command('exec python3 "$d/runner.py" fill'),
        timeout_s=min(FILL_TIMEOUT_S, ctx.timeout_s),
        env={**ctx.env, **runner_env(args.session), FILL_ENV: json.dumps(request)},
        stop=ctx.stop,
    )
    try:
        answer: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        answer = {"error": "the fill did not finish", "exit_code": result.exit_code}
    return {"login": login.origin, **answer}
