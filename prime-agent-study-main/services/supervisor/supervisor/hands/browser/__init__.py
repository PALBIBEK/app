"""The browser code mode: a session's browser, driven by code that runs in its sandbox.

The model writes Python; ``browser_exec`` (``supervisor.tools.builtin.browser_exec``)
runs it in the session's sandbox with ``runner.py``, beside a browser that outlives the
call (``daemon.py`` owns it, with a CDP connection and an event buffer), and returns
what it prints. As Hermes's ``browser_exec`` and Stagehand's ``run``:
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
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Final, Protocol

from supervisor.hands.browser.runner import META_MARK

__all__ = [
    "META_MARK",
    "RUNNER_DIR",
    "UNPACK",
    "BrowserProvider",
    "SandboxChromium",
    "pack",
    "runtime_payload",
]

RUNNER_DIR: Final = "/tmp/githi-browser/runner"
"""Where the runtime is kept in the sandbox, one folder per version of its files."""
RUNTIME_FILES: Final = ("runner.py", "daemon.py", "snapshot.py")
"""What the sandbox runs: the call's runner, the daemon it starts, and its snapshots."""


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
def runtime_payload() -> tuple[str, dict[str, str]]:
    """The runtime as the sandbox stores it: its folder there (named by its files' hash),
    and each file's source, packed (``pack``). The runner is ``<folder>/runner.py``."""
    sources = {name: resources.files(__package__).joinpath(name).read_bytes() for name in RUNTIME_FILES}
    version = hashlib.sha256(b"".join(sources[name] for name in RUNTIME_FILES)).hexdigest()[:12]
    return f"{RUNNER_DIR}/{version}", {name: pack(data) for name, data in sources.items()}


def pack(data: bytes) -> str:
    """Compressed, then base64: what fits in a shell argument and ``UNPACK`` restores."""
    return base64.b64encode(zlib.compress(data, 9)).decode("ascii")


UNPACK: Final = "import base64,sys,zlib;sys.stdout.buffer.write(zlib.decompress(base64.b64decode(sys.argv[1])))"
"""The sandbox's side of ``pack``: Python's own, as the runtime needs Python anyway."""
