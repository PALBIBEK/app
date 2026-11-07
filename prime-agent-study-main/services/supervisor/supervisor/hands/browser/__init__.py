"""The browser code mode: a session's browser, driven by code that runs in its sandbox.

The model writes Python; ``browser_exec`` (``supervisor.tools.builtin.browser_exec``)
runs it in the session's sandbox with ``runner.py``, beside a browser that outlives the
call (``daemon.py`` owns it, with a CDP connection and an event buffer), and returns
what it prints. As Hermes's ``browser_exec`` and Stagehand's ``run``:
one call does a whole sub-procedure (open, fill, click, check, extract), its steps never
cross the network, and only its output reaches the model's context.

Where the browser comes from is a seam (``providers``): a sandbox profile names its
browser, and the session manager tells each ``browser_exec`` call how to reach it, a
Chromium in the sandbox itself or a hosted one over CDP.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import shlex
import zlib
from importlib import resources
from typing import Final

from supervisor.hands.browser.providers import (
    BROWSER_PROVIDERS,
    BrowserProvider,
    BrowserUnreachable,
    RemoteCdp,
    SandboxChromium,
    browser_providers_from_env,
)
from supervisor.hands.browser.runner import CDP_ENV, DIALOG_ENV, FILL_ENV, META_MARK

__all__ = [
    "BROWSER_ERROR_ENV",
    "BROWSER_PROVIDERS",
    "BROWSER_TOOLS",
    "CDP_ENV",
    "DIALOG_ENV",
    "FILL_ENV",
    "META_MARK",
    "RUNNER_DIR",
    "UNPACK",
    "WORKSPACE_DIR",
    "BrowserProvider",
    "BrowserUnreachable",
    "RemoteCdp",
    "SandboxChromium",
    "browser_providers_from_env",
    "pack",
    "runner_env",
    "runtime_command",
    "runtime_payload",
]

BROWSER_TOOLS: Final = frozenset({"browser_exec", "browser_vault", "browser_dialog", "browser_cdp"})
"""The tools that need the session's browser provider: only their calls ask it."""
BROWSER_ERROR_ENV: Final = "GITHI_BROWSER_ERROR"
"""Why the session's browser provider could not serve this call, for the runner to report."""
WORKSPACE_DIR: Final = ".githi/browser"
"""The browser's folder in the session's workspace: saved with the session, so batch
files and the agent's own helpers survive a lost sandbox (the open tabs do not)."""
RUNNER_DIR: Final = "/tmp/githi-browser/runner"
"""Where the runtime is kept in the sandbox, one folder per version of its files."""
RUNTIME_FILES: Final = (
    "runner.py", "daemon.py", "snapshot.py", "cdp_supervisor.py", "cdp_dialogs.py", "cdp_frames.py", "cdp_socket.py",
)
"""What the sandbox runs: the call's runner, the daemon it starts, its snapshots, and the
daemon's CDP supervisor (its dialog and frame halves, and its WebSocket)."""


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


def runtime_command(run: str) -> str:
    """A shell command that stores the runtime in the sandbox once per version (in ``$d``),
    then runs ``run``, which finds the runner at ``"$d/runner.py"``."""
    folder, sources = runtime_payload()
    unpack = shlex.quote(UNPACK)
    store = " && ".join(
        f'python3 -c {unpack} {source} > "$d/{name}.$$" && mv "$d/{name}.$$" "$d/{name}"'
        for name, source in sources.items()
    )
    return f'd={shlex.quote(folder)}\n[ -f "$d/runner.py" ] || {{ mkdir -p "$d" && {store}; }}\n{run}'


def runner_env(session: str | None) -> dict[str, str]:
    """What the runner is told about the call: its workspace folder, and its browser's name."""
    return {"BROWSER_WORKSPACE": WORKSPACE_DIR, **({"GITHI_BROWSER_NAME": session} if session else {})}
