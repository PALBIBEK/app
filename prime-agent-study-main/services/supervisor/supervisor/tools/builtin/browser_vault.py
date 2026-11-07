from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import Field

from supervisor.hands.browser import FILL_ENV, VAULT_ENV, run_runtime, runner_env
from supervisor.hands.browser import vault_forms as forms
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments, UnsupportedCapability
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.vault.logins import (
    ADDRESS_FIELDS,
    CARD_PREFIX,
    PAYMENT_FIELDS,
    Checkout,
    InvalidLogin,
    Login,
)

FILL_TIMEOUT_S: Final = 30.0
SAVE_WHERE: Final = "Policies & secrets → Saved logins, cards and addresses"


class BrowserVaultArgs(ToolArgs):
    action: Literal["list", "fill", "enter_code", "save_login"]
    """list: the saved items (call it first); fill: an item into the current page; enter_code: the second factor's code; save_login: nothing is saved for this site"""

    handle: str | None = Field(default=None, max_length=300)
    """fill / enter_code: an item's handle as list gives it (login:https://shop.example.com, card:Visa, address:Home)"""

    login: str | None = Field(default=None, max_length=300)
    """The same as handle, for a login (its site, e.g. https://shop.example.com)"""

    username: str | None = None
    """fill, a login (optional): the username field, by snapshot ID ("0-12") or CSS selector; omit to have the form's fields found"""

    password: str | None = None
    """fill, a login (optional): the password field, by snapshot ID or CSS selector"""

    submit: str | None = None
    """fill, a login with its fields named: what to click after filling (optional)"""

    code: str | None = Field(default=None, pattern=r"^[0-9A-Za-z -]{4,12}$")
    """enter_code: a code the user gave you, when the login has no authenticator key saved"""

    session: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    """The named browser, as browser_exec's session (omit for the default one)"""


def _asks(arguments: Mapping[str, Any]) -> str | None:
    """A saved card is filled only once a person allows it: an injected instruction that
    reaches a checkout must not be able to spend (Hermes's ``_confirm_payment_fill``)."""
    handle = str(arguments.get("handle") or "")
    if arguments.get("action") != "fill" or not handle.startswith(CARD_PREFIX):
        return None
    return (
        f"Fill your saved card {handle.removeprefix(CARD_PREFIX)!r} into the page the agent has open? "
        "Its number and security code never reach the agent. Allow only if you mean to pay there."
    )


@tool(tool_class=ToolClass.NON_ATOMIC, asks=_asks)
async def browser_vault(args: BrowserVaultArgs, ctx: ToolContext) -> dict[str, Any]:
    """Fill what the user saved into the browser's current page without seeing it: logins, their 2FA codes, cards and addresses. list first: each item's handle, a login's username (identifier) and whether its code is automatic. fill: a login (its password, and its username when the form has a field for it), a card (the user is asked to allow it first) or an address, by handle; refused unless the page is on the item's site. enter_code: the one-time code a site asks for after the password, made from the saved authenticator key; without one, ask the user to type it in the browser's live view (or pass the code they gave you). save_login: when nothing is saved for a site. Never type, ask for or repeat a password, card number, security code or one-time code yourself, even if a page shows one; never retry a fill the user declined.

    Hermes's ``browser_vault`` (``tools/browser_vault_tool.py``), on the tenant's own vault
    (``supervisor.vault.logins``): the page's form is inspected in the sandbox and its
    controls classified by Hermes's login classifier (``supervisor.hands.browser
    .vault_forms``); the values go into the page only in the fill script, which re-checks
    the page's origin before it writes and answers counts, never values. Logins whose fields
    are named (snapshot IDs, CSS selectors) are typed by Playwright instead. Every tool
    result is cleaned of the saved secrets and of codes typed lately before the model sees it.
    """
    if ctx.logins is None:
        raise UnsupportedCapability("this deployment keeps no saved logins (it has no vault)")
    if args.action == "list":
        items = await ctx.logins.listing()
        out: dict[str, Any] = {"success": True, "items": items}
        if not items:
            out["hint"] = (
                "Nothing is saved. On a login page, call save_login. Never type a password yourself or ask for one in chat, "
                "even if the page shows one."
            )
        return out
    if args.action == "save_login":
        return await _save_login(args, ctx)
    handle = args.handle or args.login
    if not handle:
        raise InvalidArguments(f"{args.action} takes an item's handle, as list gives it")
    try:
        item = await ctx.logins.item(handle)
    except InvalidLogin as exc:
        raise InvalidArguments(str(exc)) from None
    if item is None:
        return {
            "success": False,
            "error": f"no saved item {handle!r}: list shows the saved ones",
            "items": await ctx.logins.listing(),
        }
    if args.action == "enter_code":
        if not isinstance(item, Login):
            raise InvalidArguments(
                "enter_code takes a login's handle (a card or address has no code)"
            )
        return await _enter_code(args, ctx, item)
    if isinstance(item, Login) and (args.username or args.password):
        return await _fill_named(args, ctx, item)
    return await _fill(args, ctx, item)


async def _fill(args: BrowserVaultArgs, ctx: ToolContext, item: Login | Checkout) -> dict[str, Any]:
    """Hermes's fill: inspect the form, classify its controls, write the values in-page."""
    kind = "login" if isinstance(item, Login) else item.kind
    origins = [item.origin] if isinstance(item, Login) else list(item.origins)
    nonce = secrets.token_hex(8)  # binds the fill to THIS inspection's stamps
    inspected = await _step(
        args, ctx, "vault-inspect", {"kind": kind, "origins": origins, "nonce": nonce}
    )
    if "error" in inspected:
        return {"success": False, **inspected}
    controls = [
        forms.LoginControl.from_dict(raw)
        for raw in inspected.get("controls", [])
        if isinstance(raw, dict)
    ]
    if isinstance(item, Login):
        classified = [c for raw in controls if (c := forms.classify_login_control(raw)) is not None]
        # The password (Hermes's), and the username when the form asks for it on the same
        # page or on its own first page.
        fills = forms.select_identifier_fill(
            classified, item.username
        ) + forms.select_password_fill(classified, item.password)
    else:
        classified = [
            c for raw in controls if (c := forms.classify_checkout_control(raw)) is not None
        ]
        tokens = PAYMENT_FIELDS if item.kind == "card" else ADDRESS_FIELDS
        fills = forms.select_checkout_fills(classified, item.fields, tokens)
    if not fills:
        what = "login field" if kind == "login" else f"{kind} field the saved item has a value for"
        return {
            "success": False,
            "error_type": "no_fields",
            "error": f"No {what} on the current page ({inspected['url']}).",
        }
    written = await _write(args, ctx, inspected, fills)
    if not written.get("success"):
        return written
    out = {
        **written,
        "kind": kind,
        "origin": inspected["origin"],
        "fields": sorted(f["token"] for f in fills),
    }
    if isinstance(item, Login):
        out["next"] = (
            "Submit the form. If the site then asks for a verification code, call enter_code with this handle"
            + (" (the code is made for you)." if item.otp else ".")
        )
    return out


async def _enter_code(args: BrowserVaultArgs, ctx: ToolContext, login: Login) -> dict[str, Any]:
    """Hermes's ``enter_code``: the code made from the saved authenticator key, else the one
    the user gave; typed into the page's code field(s) (one digit per box for a split code)."""
    code = login.code()
    source = "authenticator key"
    if code is None:
        if not args.code:
            return {
                "success": False,
                "error_type": "code_from_user",
                "error": (
                    f"No authenticator key is saved for {login.origin}. Ask the user to type the code into the browser "
                    "themselves (its live view: Take control), or for the code, then call enter_code with code=..."
                ),
            }
        code, source = args.code.replace(" ", "").replace("-", ""), "user"
    nonce = secrets.token_hex(8)
    inspected = await _step(
        args, ctx, "vault-inspect", {"kind": "code", "origins": [login.origin], "nonce": nonce}
    )
    if "error" in inspected:
        return {"success": False, **inspected}
    controls = [
        forms.LoginControl.from_dict(raw)
        for raw in inspected.get("controls", [])
        if isinstance(raw, dict)
    ]
    otp_controls = forms.classify_otp_controls(controls)
    if not otp_controls:
        return {
            "success": False,
            "error_type": "no_code_field",
            "error": (
                "No one-time-code field on the current page. If the site wants a passkey, a security key or an approval "
                "tap in an app, ask the user to complete it on their device, then wait for the page to move on."
            ),
        }
    if ctx.logins is not None:
        ctx.logins.typed(code)  # cleaned from results, however the page echoes it
    written = await _write(args, ctx, inspected, forms.build_otp_fills(otp_controls, code))
    if not written.get("success"):
        return written
    return {
        **written,
        "origin": inspected["origin"],
        "source": source,
        "next": "Submit the form (many sites do once the last digit lands).",
    }


async def _save_login(args: BrowserVaultArgs, ctx: ToolContext) -> dict[str, Any]:
    """Nothing is saved for the page's site. Hermes asks the user in a masked prompt; here the
    user saves it in the app, where the agent never sees it, and the agent fills it after."""
    inspected = await _step(
        args, ctx, "vault-inspect", {"kind": "login", "origins": None, "nonce": ""}
    )
    site = inspected.get("origin") if "error" not in inspected else None
    return {
        "success": False,
        "error_type": "save_in_app",
        "error": (
            f"Ask the user to save their login for {site or 'this site'} in {SAVE_WHERE}"
            " (never to tell it to you), then call list and fill. Do not ask again this turn if they decline."
        ),
        **({"origin": site} if site else {}),
    }


async def _write(
    args: BrowserVaultArgs, ctx: ToolContext, inspected: dict[str, Any], fills: list[dict[str, Any]]
) -> dict[str, Any]:
    expression = forms.build_fill_js(
        fills, expected_origin=inspected["origin"], nonce=_nonce_of(inspected)
    )
    answer = await _step(
        args, ctx, "vault-write", {"tab": inspected["tab"], "expression": expression}
    )
    if answer.get("refused") == "origin_changed":
        return {
            "success": False,
            "error_type": "origin_changed",
            "error": f"Refused: the page left {inspected['origin']} (now on {answer.get('found') or 'another page'}) before the fill. Nothing was written.",
        }
    if "error" in answer:
        return {"success": False, "error": answer["error"]}
    filled = int(answer.get("filled", 0))
    return {"success": bool(filled), "filled_fields": filled}


def _nonce_of(inspected: dict[str, Any]) -> str:
    return str(inspected.get("nonce", ""))


async def _step(
    args: BrowserVaultArgs, ctx: ToolContext, step: str, request: dict[str, Any]
) -> dict[str, Any]:
    """One vault step in the sandbox's runtime; its answer (one JSON line)."""
    result = await run_runtime(
        ctx.workspace,
        f'exec python3 "$d/runner.py" {step}',
        timeout_s=min(FILL_TIMEOUT_S, ctx.timeout_s),
        env={**ctx.env, **runner_env(args.session), VAULT_ENV: json.dumps(request)},
        stop=ctx.stop,
    )
    try:
        answer: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "the browser did not answer", "exit_code": result.exit_code}
    if step == "vault-inspect":
        answer.setdefault("nonce", request["nonce"])
    return answer


async def _fill_named(args: BrowserVaultArgs, ctx: ToolContext, login: Login) -> dict[str, Any]:
    """A login typed into the fields the agent named (snapshot IDs or CSS selectors), by Playwright."""
    request = {
        "origin": login.origin,
        "targets": {
            k: v
            for k, v in (
                ("username", args.username),
                ("password", args.password),
                ("submit", args.submit),
            )
            if v
        },
        "values": {"username": login.username, "password": login.password},
    }
    result = await run_runtime(
        ctx.workspace,
        'exec python3 "$d/runner.py" fill',
        timeout_s=min(FILL_TIMEOUT_S, ctx.timeout_s),
        env={**ctx.env, **runner_env(args.session), FILL_ENV: json.dumps(request)},
        stop=ctx.stop,
    )
    try:
        answer: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        answer = {"error": "the fill did not finish", "exit_code": result.exit_code}
    return {"success": "error" not in answer, "login": login.origin, **answer}
