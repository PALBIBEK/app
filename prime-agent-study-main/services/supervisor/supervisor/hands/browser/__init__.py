"""The browser code mode: a session's browser, driven by code that runs in its sandbox.

The model writes Python; ``browser_exec`` (``supervisor.tools.builtin.browser_exec``)
runs it in the session's sandbox with ``runner.py``, beside a browser that outlives the
call, and returns what it prints. As Hermes's ``browser_exec`` and Stagehand's ``run``:
one call does a whole sub-procedure (open, fill, click, check, extract), its steps never
cross the network, and only its output reaches the model's context.

Where the browser comes from is a seam, ``BrowserProvider``: what the runner is told so
it reaches the browser. ``SandboxChromium``, a headless Chromium the runner starts in the
sandbox itself, is the one provider so far.
"""

from __future__ import annotations

import base64
import functools
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Final, Protocol

from supervisor.hands.browser.runner import META_MARK

__all__ = [
    "META_MARK",
    "RUNNER_DIR",
    "BrowserProvider",
    "SandboxChromium",
    "runner_payload",
]

RUNNER_DIR: Final = "/tmp/githi-browser/runner"
"""Where the runner is kept in the sandbox, one file per runner version."""


class BrowserProvider(Protocol):
    """Where a session's browser runs, as the runner in the sandbox needs to know it."""

    @property
    def name(self) -> str: ...

    def env(self) -> Mapping[str, str]:
        """Variables for the runner (``GITHI_BROWSER_CDP``: a browser already running
        somewhere it can reach); none means it starts its own in the sandbox."""
        ...


@dataclass(frozen=True, slots=True)
class SandboxChromium:
    """A headless Chromium in the session's sandbox, started on its first call (the
    sandbox image must carry Python, Playwright and its Chromium)."""

    name: str = "sandbox-chromium"

    def env(self) -> Mapping[str, str]:
        return {}


@functools.cache
def runner_payload() -> tuple[str, str]:
    """The runner as the sandbox stores it: (its path there, its source in base64)."""
    source = resources.files(__package__).joinpath("runner.py").read_bytes()
    path = f"{RUNNER_DIR}/runner-{hashlib.sha256(source).hexdigest()[:12]}.py"
    return path, base64.b64encode(source).decode("ascii")
