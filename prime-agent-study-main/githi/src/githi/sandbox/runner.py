"""Policy enforcement around a command, spawned via an `ExecutionEnv`.

Centralising it means every resource bound -- wall clock, output size,
credentials -- is enforced once instead of being re-remembered by each
caller. The *spawn* itself belongs to `env.exec`, which owns the process
group and the kill; substituting a container or SSH backend there relocates
the shell without touching any of the policy below.

**What this does and does not guarantee.** It guarantees: no credentials in
the environment, a working directory inside the sandbox root, a hard wall
clock limit that kills the entire process group, and bounded output. It does
*not* guarantee filesystem confinement for arbitrary commands -- `cwd` is a
starting directory, not a chroot, and `cd /` works. Real containment of
arbitrary shell commands needs a container or seccomp, which is out of scope
here. Saying so plainly is better than implying an isolation level this does
not deliver; the file *tools* are separately confined by `paths.py`, and the
credential removal is what bounds the damage a shell command can do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..harness.env import ExecutionErrorCode
from ..harness.shell_output import sanitize_output
from .env import build_child_env
from .policy import SandboxPolicy


SPILL_DIR = ".githi"


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    interrupted: bool = False
    truncated: bool = False
    spill_path: str | None = None
    """Where the *complete* output was saved when `stdout` had to be trimmed.

    Relative to the sandbox root, so it works verbatim in `read` and `grep`,
    which both resolve against root and would reject an absolute host path.
    Without this, eliding the middle of a large result destroys it: the model
    has no route back to that data except re-running the command and hoping a
    narrower filter catches what it missed."""


def run_command(command: str, policy: SandboxPolicy, env: Any = None,
                interrupt: Any = None, on_output: Any = None) -> CommandResult:
    """Run a command under the policy, spawning through an `ExecutionEnv`.

    The division of labour is deliberate. `env.exec` owns everything
    backend-specific -- how to start a process, how to kill it, where it runs.
    This function owns everything *policy*-shaped: the redacted child
    environment, the output cap, the spill file. Pushing those into each
    backend is how one of them eventually ships without the redaction.
    """
    if not command.strip():
        raise ValueError("command must not be empty")

    policy.root.mkdir(parents=True, exist_ok=True)

    if env is None:
        from ..harness.env import LocalExecutionEnv

        env = LocalExecutionEnv(policy.root)

    def emit(stream: str, text: str) -> None:
        if on_output is not None:
            on_output(stream, sanitize_output(text))

    result = env.exec(command, cwd=str(policy.root),
                      env=build_child_env(policy), inherit_env=False,
                      timeout=policy.timeout_s, interrupt=interrupt,
                      on_output=emit if on_output is not None else None)

    if not result.is_ok:
        error = result.error
        # Partial output is kept: a command that timed out has usually done
        # most of its work, and what it printed before hanging is often the
        # very thing that explains the hang.
        stdout, truncated = _truncate(sanitize_output(
            getattr(error, "partial_stdout", "") or ""),
                                      policy.max_output_bytes)
        if error.code is ExecutionErrorCode.TIMEOUT:
            return CommandResult(
                stdout=stdout,
                stderr=f"command exceeded the {policy.timeout_s}s limit and was killed",
                exit_code=124,      # conventional shell exit code for timeout
                timed_out=True,
                truncated=truncated,
            )
        if error.code is ExecutionErrorCode.ABORTED:
            return CommandResult(
                stdout=stdout,
                stderr="command interrupted",
                exit_code=130,
                interrupted=True,
                truncated=truncated,
            )
        return CommandResult(stdout=stdout, stderr=str(error), exit_code=126,
                             truncated=truncated)

    # Sanitised before truncation: escape sequences are invisible characters
    # that still consume the byte budget, so trimming first would spend the
    # cap on bytes that are about to be discarded -- and could cut a sequence
    # in half, leaving its printable tail as literal garbage.
    raw_out = sanitize_output(result.value.stdout or "")
    raw_err = sanitize_output(result.value.stderr or "")
    stdout, truncated = _truncate(raw_out, policy.max_output_bytes)
    stderr, _ = _truncate(raw_err, min(policy.max_output_bytes, 4000))
    spill = _write_spill(raw_out or "", policy) if truncated else None
    return CommandResult(stdout=stdout, stderr=stderr,
                         exit_code=result.value.exit_code,
                         truncated=truncated, spill_path=spill)


def _write_spill(full_output: str, policy: SandboxPolicy) -> str | None:
    """Persist the complete output so truncation is recoverable.

    Returns None rather than raising on any failure: a spill file is a
    convenience, and losing it must never turn a successful command into a
    failed tool call. A read-only policy is respected -- `--read-only` means
    githi does not write either, and the behaviour simply degrades to plain
    truncation.
    """
    if not policy.writable:
        return None
    try:
        directory = policy.root / SPILL_DIR
        directory.mkdir(parents=True, exist_ok=True)
        # Time-based names collide when two commands land in the same
        # millisecond, which is easy inside one turn; a counter does not.
        existing = len(list(directory.glob("output-*.txt")))
        path = directory / f"output-{existing + 1:03d}.txt"
        while path.exists():                       # defensive against races
            existing += 1
            path = directory / f"output-{existing + 1:03d}.txt"
        path.write_text(full_output, encoding="utf-8")
        return str(path.relative_to(policy.root))
    except OSError:
        return None


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Keep both ends when eliding.

    Head-only truncation is the intuitive choice and the wrong one: a
    command's actual answer is usually the last thing it prints, so cutting
    the tail discards exactly the part the model needed.
    """
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return (f"{text[:half]}\n\n[... {len(text) - limit} characters elided ...]\n\n"
            f"{text[-half:]}"), True
