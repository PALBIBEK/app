"""Command line entry point.

    githi "find duplicate issues" --workspace ./scratch

Progress goes to stderr, the final answer to stdout, so `githi ... > out.txt`
captures the answer alone while still showing what happened on the terminal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .agent.agent import Agent, AgentConfig
from .agent.events import Event, EventType
from .agent.interrupt import Interrupt
from .errors import GithiError
from .repl import run_repl
from .harness.skills import SkillLibrary
from .llm.client import LLMClient
from .memory.service import MemoryService
from .sandbox.policy import SandboxPolicy
from .types import ThinkingLevel
from .tools.registry import ToolRegistry, default_tools
from .tools.remember import remember_tool


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        agent, interrupt = build_agent(args)
    except GithiError as exc:
        print(f"githi: {exc}", file=sys.stderr)
        return 2
    llm = agent.llm

    if args.prompt is None:
        try:
            return run_repl(agent, interrupt)
        finally:
            llm.close()

    # Always attached, not only under -v: the answer streams either way, and
    # -v adds the tool trace on stderr.
    printer = StreamPrinter(verbose=args.verbose)
    agent.on(printer)

    try:
        result = agent.run(args.prompt)
    except GithiError as exc:
        print(f"githi: {exc}", file=sys.stderr)
        return 1
    finally:
        llm.close()

    print(f"\n[{result.stop_reason.value}] {result.usage.summary(args.model)} | "
          f"{result.tool_calls} tool calls", file=sys.stderr)
    if result.stop_reason.value == "max_turns":
        print("[warning] stopped at the turn limit with work still pending -- this is "
              "NOT a completed run; retry with a higher --max-turns.", file=sys.stderr)

    # Only when nothing streamed -- a blocking client, or a run that produced
    # no text. Printing unconditionally would show the answer twice.
    if printer.streamed:
        print()
    else:
        print(result.output)
    return 0


def build_agent(args) -> tuple[Agent, Interrupt]:
    """Assemble everything the CLI needs, in one testable place.

    Extracted from `main` so the wiring can be asserted on directly. That
    matters here more than usual: five separate capabilities in this project
    were fully built, fully tested, and never reached from the entry point --
    memory among them, which was switched off for every real session because
    `main` simply never passed it.
    """
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    policy = SandboxPolicy(
        root=workspace,
        writable=not args.read_only,
        allow_credentials=args.allow_credentials,
        timeout_s=args.command_timeout,
    )

    skills = SkillLibrary.discover(config.skill_roots(workspace))
    # To stderr, so it is visible interactively but never mixed into piped
    # output. Silent when nothing is wrong.
    if report := skills.diagnostics_report():
        print(report, file=sys.stderr)

    builtins = default_tools()

    # Inside the workspace, so memory persists with everything else there and
    # a different --workspace is a genuinely separate memory.
    memory = None
    if not args.no_memory:
        memory = MemoryService(root=workspace / "memory")
        builtins = [*builtins, remember_tool(memory)]

    llm = LLMClient(api_key=config.api_key(), model=args.model,
                    base_url=config.base_url(),
                    enable_caching=not args.no_cache)

    interrupt = Interrupt()
    agent = Agent(
        llm=llm, tools=ToolRegistry(builtins), policy=policy,
        config=AgentConfig(max_turns=args.max_turns,
                           skill_listing=skills.listing(),
                           cwd=str(workspace),
                           tools_for_prompt=builtins,
                           interrupt=interrupt,
                           memory=memory,
                           session_path=workspace / "session.jsonl"),
    )
    # Pi defaults thinking to medium. `off` leaves the request without a
    # reasoning object so models that reject it stay usable.
    if getattr(args, "thinking", "medium") != "off":
        agent.set_thinking_level(ThinkingLevel(args.thinking))
    return agent, interrupt


class StreamPrinter:
    """Renders a one-shot run as it happens.

    The answer streams to **stdout** and tool activity goes to **stderr**, so
    `githi "..." > answer.txt` captures the answer and nothing else while the
    progress remains visible on the terminal.

    Before this, one-shot printed tool calls and then nothing at all for the
    ten to thirty seconds the model spent writing its answer -- silence that
    is indistinguishable from a hang, and was reported as one. The loop was
    emitting `TEXT_DELTA` the whole time; nothing was listening.
    """

    def __init__(self, out=None, err=None, verbose: bool = False) -> None:
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self.verbose = verbose
        self.streamed = False
        """Whether any answer text was printed. The caller skips its own final
        print when this is set, or the answer appears twice."""
        self._mid_line = False

    def __call__(self, event: Event) -> None:
        if event.type is EventType.THINKING_START:
            if self._mid_line:
                self.out.write("\n")
                self.out.flush()
                self._mid_line = False
            print("Thinking:", file=self.err, flush=True)
            return

        if event.type is EventType.THINKING_DELTA:
            delta = event.data.get("delta", "")
            if delta:
                self.err.write(delta)
                self.err.flush()
            return

        if event.type is EventType.THINKING_END:
            self.err.write("\n")
            self.err.flush()
            return

        if event.type is EventType.TEXT_DELTA:
            delta = event.data.get("delta", "")
            if delta:
                self.out.write(delta)
                self.out.flush()
                self.streamed = True
                self._mid_line = True
            return

        if not self.verbose:
            return

        # Streamed text arrives without a trailing newline, so anything printed
        # during a stream has to close the line or it lands mid-sentence.
        if self._mid_line:
            self.out.write("\n")
            self.out.flush()
            self._mid_line = False

        if event.type is EventType.MEMORY_RECALL:
            print(f"  [memory: recalled {event.data.get('count')} note(s)]",
                  file=self.err, flush=True)
        elif event.type is EventType.TOOL_START:
            args = str(event.data.get("args", ""))[:160]
            print(f"  -> {event.data.get('tool')} {args}", file=self.err, flush=True)
        elif event.type is EventType.TOOL_END:
            mark = "blocked" if event.data.get("blocked") else (
                "error" if event.data.get("is_error") else "ok")
            print(f"  <- {event.data.get('tool')} [{mark}]", file=self.err, flush=True)
        elif event.type is EventType.TURN_END:
            print(f"  [turn {event.data.get('turn')}: "
                  f"{event.data.get('tool_calls')} tool call(s)]",
                  file=self.err, flush=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="githi", description="A sandboxed agent harness.")
    # Optional: with no prompt githi starts an interactive session. That is
    # the more common way to use it now, but making it the *only* way would
    # break every existing script that passes a prompt.
    p.add_argument("prompt", nargs="?",
                   help="a single prompt to run; omit for an interactive session")
    p.add_argument("--workspace", default="./githi-workspace",
                   help="sandbox root; all file access is confined here")
    p.add_argument("--model", default=config.model())
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--command-timeout", type=int, default=30,
                   help="seconds a single bash command may run")
    p.add_argument("--read-only", action="store_true",
                   help="refuse all file mutations")
    p.add_argument("--allow-credentials", action="store_true",
                   help="pass API tokens through to the sandbox. Off by default: "
                        "with no credentials a shell command can reach the network "
                        "but cannot authenticate, which is what bounds the damage")
    p.add_argument("--no-cache", action="store_true", help="disable prompt caching")
    p.add_argument("--no-memory", action="store_true",
                   help="disable long-term memory (recall and the remember tool)")
    p.add_argument("--thinking", default="medium",
                   choices=[level.value for level in ThinkingLevel],
                   help="reasoning effort sent to OpenRouter (pi's --thinking; "
                        "default medium). Use off to omit the reasoning request.")
    p.add_argument("-v", "--verbose", action="store_true", help="print tool activity")
    return p.parse_args(argv)


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
