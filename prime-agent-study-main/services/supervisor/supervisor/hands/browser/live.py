"""The browser as a person sees it, and their hand on it: the live view and takeover.

The agent's browser runs in the session's sandbox (``daemon.py``). A person can watch its
current tab (``screen``) whenever it is open, and act on it (``act``: a click, typing, a
key, a scroll) while the agent is not working, for what only they can do there: a CAPTCHA,
a two-factor code, a payment they confirm themselves. Each look runs ``runner.py live`` in
the sandbox, as a call does, so it reaches the same browser and changes nothing a call
reads; it never starts a browser.

What a person types reaches the page and nothing else: it is not journaled or logged, and
the agent learns what changed from the person's own message when they hand back.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from supervisor.hands.browser import runner_env, runtime_command
from supervisor.tools.workspace import Workspace

__all__ = ["LIVE_TIMEOUT_S", "Input", "LiveRefused", "Screen", "act", "screen"]

LIVE_TIMEOUT_S: Final = 20.0
"""A look, or an input and the look after it: Playwright connecting, then a screenshot."""
_LIVE_ENV: Final = "GITHI_BROWSER_LIVE"  # runner.LIVE_ENV: the runtime imports nothing from here


class LiveRefused(Exception):
    """The browser cannot be shown or acted on: no browser is open, or the input is not one."""


class Screen(BaseModel):
    """The browser's current tab, as the person sees it."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    image: str
    """A base64 JPEG of the tab's viewport, ``width`` by ``height`` CSS pixels."""


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Click(_Input):
    kind: Literal["click"]
    x: float = Field(ge=0, le=10_000)
    y: float = Field(ge=0, le=10_000)


class Type(_Input):
    kind: Literal["type"]
    text: str = Field(min_length=1, max_length=1_000)


class Key(_Input):
    kind: Literal["key"]
    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9+]+$")
    """Playwright's name for it (``Enter``, ``Tab``, ``Control+a``); the runtime checks the name"""


class Scroll(_Input):
    kind: Literal["scroll"]
    x: float = Field(default=0, ge=0, le=10_000)
    y: float = Field(default=0, ge=0, le=10_000)
    dx: float = Field(default=0, ge=-10_000, le=10_000)
    dy: float = Field(default=0, ge=-10_000, le=10_000)


type Input = Annotated[Click | Type | Key | Scroll, Field(discriminator="kind")]


async def screen(workspace: Workspace, env: Mapping[str, str]) -> Screen:
    """The browser's current tab, now. ``env`` says how the runtime reaches the browser (the
    session's browser provider), as for a call."""
    return await _live(workspace, env, {})


async def act(workspace: Workspace, env: Mapping[str, str], action: Input) -> Screen:
    """Do ``action`` on the current tab; the tab after it."""
    return await _live(workspace, env, {"input": action.model_dump()})


async def _live(workspace: Workspace, env: Mapping[str, str], request: dict[str, Any]) -> Screen:
    result = await workspace.run(
        runtime_command('exec python3 "$d/runner.py" live'),
        timeout_s=LIVE_TIMEOUT_S,
        env={**env, **runner_env(None), _LIVE_ENV: json.dumps(request)},
    )
    lines = result.stdout.strip().splitlines()
    try:
        answer = json.loads(lines[-1]) if lines else {}
    except ValueError:
        answer = {}
    if "error" in answer:
        raise LiveRefused(str(answer["error"]))
    try:
        return Screen.model_validate(answer)
    except ValidationError:
        detail = (result.stderr.strip().splitlines() or [f"exit {result.exit_code}"])[-1][:300]
        raise LiveRefused(f"the browser could not be shown: {detail}") from None

