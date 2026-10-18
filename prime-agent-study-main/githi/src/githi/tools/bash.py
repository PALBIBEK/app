"""The bash tool -- githi's single most valuable capability.

One shell command chains a dozen operations in a *single* tool call, where a
tool-per-operation agent pays a full model round trip for each. With no
compaction that round trip also re-sends the whole growing transcript, so
turn count drives cost super-linearly. This is the difference between
answering a question in three calls and answering it in twenty.

All the enforcement lives in `sandbox.runner`; this file is the model-facing
contract around it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..sandbox.runner import run_command
from .base import Tool, ToolContext, ToolResult, fail, ok

# Ported from pi's bash.ts: purely mechanical -- what the tool does, what it
# returns, what happens when output is too large. Pi says nothing here about
# networks, credentials, cost or strategy, and that restraint is the point.
# The earlier hand-written version warned that "authenticated network calls
# cannot succeed -- do not waste a call trying one", which the model
# generalised into "the network is closed" and stopped considering `git clone`.
DESCRIPTION = (
    "Execute a bash command in the current working directory. Returns stdout "
    "and stderr. Output is truncated if it exceeds the configured limit; when "
    "truncated, the full output is saved to a file inside the working "
    "directory and its path is reported so you can read or grep it. "
    "Optionally provide a timeout in seconds."
)


def bash_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args.get("command") or "")
        if not command.strip():
            return fail("bash requires a non-empty `command`.")

        policy = ctx.policy
        notices: list[str] = []
        requested = args.get("timeout")
        if requested is not None:
            try:
                # A model-supplied timeout is a request, never an override:
                # SandboxPolicy clamps it to max_timeout_s in __post_init__.
                policy = replace(policy, timeout_s=max(1, int(requested)))
            except (TypeError, ValueError):
                return fail("`timeout` must be a whole number of seconds.")
            if policy.timeout_was_clamped:
                # Told, not silently applied: otherwise a killed command reads
                # as "too slow" instead of "your timeout was overridden", and
                # the model optimises the wrong thing.
                notices.append(f"[requested timeout {int(requested)}s exceeds the "
                               f"{policy.max_timeout_s}s limit; {policy.timeout_s}s "
                               "was applied]")

        registry = ctx.extra.get("bash_runs")
        local_interrupt = registry.register() if registry else ctx.extra.get("interrupt")
        try:
            result = run_command(
                command, policy, env=ctx.env,
                interrupt=local_interrupt,
                on_output=ctx.extra.get("on_output"),
            )
        finally:
            if registry:
                registry.unregister(local_interrupt)

        parts: list[str] = list(notices)
        if result.stdout.strip():
            parts.append(result.stdout.rstrip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if result.truncated:
            if result.spill_path:
                # Naming the path is the whole point -- a spill file the model
                # is not told about might as well not exist.
                parts.append(
                    f"[output truncated. The COMPLETE output was saved to "
                    f"{result.spill_path} -- read or grep that file to get the part "
                    f"elided above, instead of re-running the command.]")
            else:
                parts.append("[output truncated -- narrow the command or pipe through "
                             "`head` to return less]")
        if not parts:
            # An empty tool result gives the model nothing to reason about,
            # and it will often re-run the same command to find out what
            # happened. Saying "it worked, silently" costs one line.
            parts.append(f"[no output; exit code {result.exit_code}]")

        content = "\n".join(parts)
        if result.interrupted:
            return ok(content, exit_code=result.exit_code, interrupted=True)
        if result.timed_out or result.exit_code != 0:
            return fail(content, exit_code=result.exit_code, timed_out=result.timed_out)
        return ok(content, exit_code=0)

    return Tool(
        name="bash",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "the bash command to run"},
                "timeout": {"type": "integer",
                            "description": "seconds before the command is killed"},
            },
            "required": ["command"],
        },
        run=run,
    )
