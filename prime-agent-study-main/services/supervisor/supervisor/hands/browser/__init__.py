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
import threading
import time
import zlib
from collections.abc import Mapping
from importlib import resources
from typing import TYPE_CHECKING, Final

from supervisor.hands.browser.providers import (
    BROWSER_PROVIDERS,
    HOSTED_BROWSERS,
    Browserbase,
    BrowserProvider,
    BrowserUnreachable,
    BrowserUse,
    Firecrawl,
    RemoteCdp,
    SandboxChromium,
    SandboxLightpanda,
    browser_providers_from_env,
)
from supervisor.hands.browser.runner import CDP_ENV, DIALOG_ENV, FILL_ENV, META_MARK, VAULT_ENV

if TYPE_CHECKING:
    from supervisor.tools.workspace import CommandResult, Workspace

__all__ = [
    "BROWSER_ERROR_ENV",
    "BROWSER_FALLBACK_ENV",
    "BROWSER_PROVIDERS",
    "BROWSER_TOOLS",
    "CDP_ENV",
    "DIALOG_ENV",
    "FILL_ENV",
    "HOSTED_BROWSERS",
    "META_MARK",
    "MISSING_EXIT",
    "MISSING_MARK",
    "RUNNER_DIR",
    "UNPACK",
    "VAULT_ENV",
    "WORKSPACE_DIR",
    "BrowserProvider",
    "BrowserUnreachable",
    "BrowserUse",
    "Browserbase",
    "Firecrawl",
    "RemoteCdp",
    "SandboxChromium",
    "SandboxLightpanda",
    "browser_providers_from_env",
    "install_command",
    "pack",
    "run_runtime",
    "runner_env",
    "runtime_command",
    "runtime_payload",
]

BROWSER_TOOLS: Final = frozenset({"browser_exec", "browser_vault", "browser_dialog", "browser_cdp"})
"""The tools that need the session's browser provider: only their calls ask it."""
BROWSER_ERROR_ENV: Final = "GITHI_BROWSER_ERROR"
"""Why the session's browser provider could not serve this call, for the runner to report."""
BROWSER_FALLBACK_ENV: Final = "GITHI_BROWSER_FALLBACK"
"""Why the session's hosted browser could not serve this call, so the sandbox's own serves it
(Hermes's fallback from a cloud browser to a local one), for the runner to report."""
WORKSPACE_DIR: Final = ".githi/browser"
"""The browser's folder in the session's workspace: saved with the session, so batch
files and the agent's own helpers survive a lost sandbox (the open tabs do not)."""
RUNNER_DIR: Final = "/tmp/githi-browser/runner"
"""Where the runtime is kept in the sandbox, one folder per version of its files."""
RUNTIME_FILES: Final = (
    "runner.py", "daemon.py", "snapshot.py", "vault_forms.py",
    "cdp_supervisor.py", "cdp_dialogs.py", "cdp_frames.py", "cdp_socket.py",
)
"""What the sandbox runs: the call's runner, the daemon it starts, its snapshots, the vault's
form finder, and the daemon's CDP supervisor (its dialog and frame halves, and its WebSocket)."""


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


MISSING_EXIT: Final = 97
"""What a runtime command exits with when the sandbox has no copy of this runtime yet."""
MISSING_MARK: Final = "githi-browser: runtime missing"


def runtime_command(run: str) -> str:
    """A shell command that runs ``run`` with the runtime's folder in ``$d`` (``run`` finds
    the runner at ``"$d/runner.py"``), or exits ``MISSING_EXIT`` when the sandbox has not
    stored this version of the runtime yet (``run_runtime`` stores it, then runs it again).
    The runtime is not in the command: with it, a call's code would not fit the one shell
    argument a command is (Linux caps it at 128 KiB)."""
    folder, _ = runtime_payload()
    return (
        f'd={shlex.quote(folder)}\n[ -f "$d/.complete" ] || {{ echo {shlex.quote(MISSING_MARK)} >&2; exit {MISSING_EXIT}; }}\n{run}'
    )


def install_command() -> str:
    """A shell command that stores this version of the runtime in the sandbox: each file
    written aside and moved into place, the folder marked complete last (two calls storing
    it at once each leave it whole)."""
    folder, sources = runtime_payload()
    unpack = shlex.quote(UNPACK)
    store = " && ".join(
        f'python3 -c {unpack} {source} > "$d/{name}.$$" && mv "$d/{name}.$$" "$d/{name}"' for name, source in sources.items()
    )
    return f'd={shlex.quote(folder)}\nmkdir -p "$d" && {store} && touch "$d/.complete"'


async def run_runtime(
    workspace: Workspace,
    run: str,
    *,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    stop: threading.Event | None = None,
) -> CommandResult:
    """``run`` (as ``runtime_command`` takes it) in the sandbox, storing the runtime first
    when this sandbox has no copy of this version yet."""
    started = time.monotonic()
    result = await workspace.run(runtime_command(run), timeout_s=timeout_s, env=env, stop=stop)
    if result.exit_code != MISSING_EXIT or MISSING_MARK not in result.stderr:
        return result
    installed = await workspace.run(install_command(), timeout_s=timeout_s, stop=stop)
    if installed.exit_code != 0:
        return installed  # its stderr says why (no python3 in the sandbox, say)
    left = max(timeout_s - (time.monotonic() - started), 1.0)
    return await workspace.run(runtime_command(run), timeout_s=left, env=env, stop=stop)


def runner_env(session: str | None) -> dict[str, str]:
    """What the runner is told about the call: its workspace folder, and its browser's name."""
    return {"BROWSER_WORKSPACE": WORKSPACE_DIR, **({"GITHI_BROWSER_NAME": session} if session else {})}
