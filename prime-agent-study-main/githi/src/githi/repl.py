"""The interactive terminal loop: prompt, stream, interrupt, repeat.

Inline scrollback rather than a full-screen application, matching pi and
Claude Code. The difference is not cosmetic: a full-screen app draws into the
alternate buffer, so everything the agent said vanishes from the terminal the
moment you quit. Inline output stays in scrollback, where it can be searched,
copied and piped like any other command's output.

Rendering is deliberately thin -- ANSI colour and a spinner, no widget
framework. What the user actually needs is to see text as it arrives, know
which tool is running, and be able to stop it. A layout engine buys none of
that.

**Why the reader thread.** ESC has to be noticed *while* the agent is
streaming and running tools. The thread watches stdin the whole time the
agent works and sets a flag the loop polls; the agent core stays synchronous.
The interrupt is therefore cooperative -- it lands at the next poll, between
chunks or between tools -- which is fast enough to feel instant and cost
nothing in complexity elsewhere.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import TextIO

from .agent.agent import Agent
from .agent.events import Event, EventType
from .agent.interrupt import Interrupt, KeyReader
from .errors import AskUserError
from .sandbox.runner import CommandResult, run_command
from .tools.ask_user import AskAnswer, AskAnswerItem, AskQuestion
from .types import StopReason, ThinkingLevel, UserMessage

DIM = "\x1b[2m"
BOLD = "\x1b[1m"
ITALIC = "\x1b[3m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"

BANNER_TEXT = "githi -- ESC interrupts, Ctrl-D exits"


class TerminalInput:
    """A small, replaceable line editor for the idle half of the REPL.

    `prompt_toolkit` owns terminal cleanup while it is prompting, and gives the
    interactive shell multiline editing, history, and bracketed-paste handling.
    Piped stdin deliberately keeps the old `readline` behaviour: interactive
    terminal affordances must not change scripts or tests.
    """

    def __init__(self, stream: TextIO, out: TextIO) -> None:
        self.stream = stream
        self.out = out
        self._session = None

    def read(self, prompt: str, *, abortable: bool = False) -> str | None:
        if not self.stream.isatty() or not self.out.isatty():
            self.out.write(prompt)
            self.out.flush()
            line = self.stream.readline()
            return line if line else None

        from prompt_toolkit.formatted_text import ANSI

        # ask_user_question: KeyReader is paused, so ESC must abort here.
        # One-off session; single-line — choice/custom answers are not multiline
        # prompts, and Escape must not fight line-editing.
        if abortable:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.key_binding import KeyBindings

            bindings = KeyBindings()

            @bindings.add("escape", eager=True)
            def _escape_abort(event) -> None:
                event.app.exit(exception=KeyboardInterrupt())

            session = PromptSession(key_bindings=bindings)
            try:
                return session.prompt(ANSI(prompt))
            except EOFError:
                return None

        if self._session is None:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings

            bindings = KeyBindings()

            # Shift+Enter for a newline. prompt_toolkit has no `s-enter` key
            # (ValueError on bind); terminals that honour Shift+Enter for a
            # line break -- Cursor, VS Code, and most macOS ones -- deliver it
            # as LF, which is `c-j`. Plain Enter stays CR (`enter` / `c-m`) and
            # submits.
            @bindings.add("c-j")
            def _newline(event) -> None:
                event.current_buffer.insert_text("\n")

            @bindings.add("enter")
            def _submit(event) -> None:
                event.current_buffer.validate_and_handle()

            self._session = PromptSession(
                history=InMemoryHistory(),
                multiline=True,
                key_bindings=bindings,
                prompt_continuation="… ",
            )

        try:
            # Raw ANSI in a plain str is shown literally (`^[[1m…`). Wrap so
            # prompt_toolkit interprets the renderer’s colour codes.
            return self._session.prompt(ANSI(prompt))
        except EOFError:
            return None
        except KeyboardInterrupt:
            # Idle REPL treats Ctrl-C as an empty line.
            return ""

class InFlightInput:
    """Collect terminal input while the synchronous agent is working.

    A plain ESC is delayed very briefly because terminals encode Alt+Enter as
    ESC followed by Enter. That preserves Pi's useful distinction: Enter
    steers the active run; Alt+Enter queues a follow-up for after it settles.
    """

    ESC_DELAY_SECONDS = 0.075

    def __init__(self, agent: Agent, interrupt: Interrupt, renderer: "Renderer") -> None:
        self.agent = agent
        self.interrupt = interrupt
        self.renderer = renderer
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._escape_timer: threading.Timer | None = None
        self._bash_cancelled = False

    def close(self) -> None:
        with self._lock:
            if self._escape_timer:
                self._escape_timer.cancel()
                self._escape_timer = None

    def on_key(self, key: str) -> bool:
        """Handle a raw key and return whether it consumed the key."""
        with self._lock:
            if key == "\x1b":
                if not self._bash_cancelled and self.agent.bash_runs.abort_all():
                    self._bash_cancelled = True
                    self.renderer.status("[bash interrupt requested; ESC again aborts agent]")
                elif self.agent.abort_owned_background():
                    self.renderer.status(
                        "[subagent interrupt requested; ESC again aborts agent]"
                    )
                elif self.interrupt.is_set() or self._bash_cancelled:
                    self.interrupt.set()
                    self.renderer.status("[exiting after interrupt cleanup]")
                elif self._escape_timer is not None:
                    # A second ESC is an explicit escalation. The REPL exits
                    # after the reader has restored the terminal.
                    self._escape_timer.cancel()
                    self._escape_timer = None
                    self.interrupt.set()
                    self.interrupt.set()
                    self.renderer.status("[exiting after interrupt cleanup]")
                else:
                    self._escape_timer = threading.Timer(
                        self.ESC_DELAY_SECONDS, self._interrupt)
                    self._escape_timer.daemon = True
                    self._escape_timer.start()
                return True

            follow_up = self._escape_timer is not None and key in "\r\n"
            if self._escape_timer is not None:
                self._escape_timer.cancel()
                self._escape_timer = None

            if key in "\r\n":
                text = "".join(self._buffer).strip()
                self._buffer.clear()
                if text:
                    if follow_up:
                        self.agent.follow_up(UserMessage(text))
                        self.renderer.status("[queued follow-up]")
                    else:
                        self.agent.steer(UserMessage(text))
                        self.renderer.status("[queued steering message]")
                return True

            if key in ("\x7f", "\b"):
                if self._buffer:
                    self._buffer.pop()
            elif key >= " ":
                self._buffer.append(key)
            return True

    def _interrupt(self) -> None:
        with self._lock:
            self._escape_timer = None
        self.agent.abort_owned_background()
        self.interrupt.set()
        self.renderer.status("[interrupt requested]")


class Spinner:
    """A heartbeat while a tool runs.

    Tool execution is where an agent spends most of its wall clock, and it
    blocks the main thread — so without a separate thread there is nothing
    left to update the display, and the terminal simply freezes. With a
    120-second command timeout that is two minutes in which "working" and
    "hung" look identical.

    The elapsed seconds matter more than the animation: a spinning character
    could itself be a stuck loop, whereas a number that visibly climbs proves
    time is passing and the call has not wedged.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, out=None, enabled: bool = True,
                 interval: float = 0.1) -> None:
        import sys as _sys

        self.out = out if out is not None else _sys.stderr
        self.enabled = enabled
        self.interval = interval
        self.running = False
        self._thread = None
        self._stop = threading.Event()

    def start(self, label: str) -> None:
        if not self.enabled:
            return
        self.stop()                       # never leak a previous thread
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._spin, args=(label,), daemon=True)
        self._thread.start()

    def _spin(self, label: str) -> None:
        started = time.time()
        frame = 0
        while not self._stop.wait(self.interval):
            elapsed = time.time() - started
            try:
                self.out.write(
                    f"\r    {self.FRAMES[frame % len(self.FRAMES)]} {label} "
                    f"{elapsed:.0f}s ")
                self.out.flush()
            except Exception:  # noqa: BLE001 - a closed stream must not crash a run
                return
            frame += 1

    def stop(self) -> None:
        """Stop and erase the line, so the caller's output starts clean."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.running:
            try:
                self.out.write("\r" + " " * 48 + "\r")
                self.out.flush()
            except Exception:  # noqa: BLE001
                pass
        self.running = False


@dataclass
class Renderer:
    """Turns agent events into terminal output.

    Holds one piece of state -- whether we are mid-line -- because streamed
    text arrives without newlines and anything else printed during a stream
    (a tool call, a status line) has to break the line first or it lands in
    the middle of the model's sentence.
    """

    out: TextIO
    colour: bool = True
    hide_thinking: bool = False
    """Pi's hideThinkingBlock: when True, stream a single 'Thinking...' label
    instead of the reasoning text."""
    _in_text: bool = False
    _in_thinking: bool = False
    spinner: Spinner | None = None
    _tool_output: dict[str, str] = None  # type: ignore[assignment]
    _text_buffer: str = ""
    _thinking_buffer: str = ""

    def __post_init__(self) -> None:
        self._tool_output = {}

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.colour else text

    def _break_line(self) -> None:
        self.flush_text()
        self.flush_thinking()
        if self._in_text or self._in_thinking:
            self.out.write("\n")
            self._in_text = False
            self._in_thinking = False

    def on_event(self, event: Event) -> None:
        if event.type is EventType.THINKING_START:
            self._stop_spinner()
            self.flush_text()
            if self.hide_thinking:
                self._break_line()
                self.out.write(self._c(DIM + ITALIC, "Thinking...") + "\n")
                self.out.flush()
            else:
                self._break_line()
                self.out.write(self._c(DIM + ITALIC, "Thinking:\n"))
                self.out.flush()
                self._in_thinking = True

        elif event.type is EventType.THINKING_DELTA:
            if self.hide_thinking:
                return
            self._stop_spinner()
            self._thinking_buffer += event.data.get("delta", "")
            self._flush_thinking_lines()

        elif event.type is EventType.THINKING_END:
            if not self.hide_thinking:
                self.flush_thinking()
                if self._in_thinking:
                    self.out.write("\n")
                    self._in_thinking = False
                    self.out.flush()

        elif event.type is EventType.TEXT_DELTA:
            # A tool's result arriving is what ends its spinner in practice:
            # the model starts writing again the moment it has the output.
            self._stop_spinner()
            if self._in_thinking:
                self.flush_thinking()
                self.out.write("\n")
                self._in_thinking = False
            self._text_buffer += event.data.get("delta", "")
            self._flush_complete_lines()

        elif event.type is EventType.TOOL_START:
            self._break_line()
            name = event.data.get("tool", "?")
            self.out.write(self._c(CYAN, f"  · {name}") + f" {_brief(event.data.get('args'))}\n")
            self.out.flush()
            if self.spinner:
                self.spinner.start(name)

        elif event.type is EventType.TOOL_END:
            self._stop_spinner()
            result = str(event.data.get("result") or "")
            tool_id = str(event.data.get("tool_id") or event.data.get("tool") or "")
            if result:
                self._tool_output[tool_id] = result
                preview = result.splitlines()[0][:160]
                self._break_line()
                self.out.write(self._c(DIM, f"    {preview}\n"))
            if event.data.get("is_error"):
                self._break_line()
                self.out.write(self._c(RED, "    failed") + "\n")
                self.out.flush()

        elif event.type is EventType.TOOL_PROGRESS:
            self._break_line()
            prefix = "[stderr] " if event.data.get("stream") == "stderr" else ""
            self.out.write(self._c(DIM, f"    {prefix}{event.data.get('text', '')}"))
            self.out.flush()

        elif event.type is EventType.TURN_START:
            self._break_line()
            self.out.write(self._c(DIM, f"[turn {event.data.get('turn', '?')}]\n"))
            self.out.flush()

        elif event.type is EventType.TURN_END:
            self._break_line()
            self.out.write(self._c(DIM, f"[turn {event.data.get('turn', '?')}: "
                                         f"{event.data.get('tool_calls', 0)} tool call(s)]\n"))
            self.out.flush()

        elif event.type is EventType.MEMORY_RECALL:
            self._break_line()
            self.out.write(self._c(DIM, f"[memory: recalled {event.data.get('count', 0)} note(s)]\n"))
            self.out.flush()

    def status(self, text: str) -> None:
        """Render an asynchronous input/interrupt status without corrupting text."""
        self._break_line()
        self.out.write(self._c(DIM, f"{text}\n"))
        self.out.flush()

    def _flush_complete_lines(self) -> None:
        while "\n" in self._text_buffer:
            line, self._text_buffer = self._text_buffer.split("\n", 1)
            self.out.write(_format_markdown(line, colour=self.colour) + "\n")
            self._in_text = True
        self.out.flush()

    def _flush_thinking_lines(self) -> None:
        while "\n" in self._thinking_buffer:
            line, self._thinking_buffer = self._thinking_buffer.split("\n", 1)
            self.out.write(self._c(DIM + ITALIC, line) + "\n")
            self._in_thinking = True
        self.out.flush()

    def flush_thinking(self) -> None:
        if self._thinking_buffer:
            self.out.write(self._c(DIM + ITALIC, self._thinking_buffer))
            self._thinking_buffer = ""
            self._in_thinking = True
            self.out.flush()

    def flush_text(self) -> None:
        if self._text_buffer:
            self.out.write(_format_markdown(self._text_buffer, colour=self.colour))
            self._text_buffer = ""
            self._in_text = True
            self.out.flush()

    def expand(self, target: str) -> bool:
        result = self._tool_output.get(target)
        if result is None:
            return False
        self._break_line()
        self.out.write(f"[tool output: {target}]\n{result.rstrip()}\n")
        self.out.flush()
        return True

    def collapse(self) -> None:
        self.status("[tool output collapsed]")

    def _stop_spinner(self) -> None:
        if self.spinner:
            self.spinner.stop()

    def finish(self, agent: Agent, result) -> None:
        self._stop_spinner()
        self.flush_thinking()
        self.flush_text()
        self._break_line()
        reason = result.stop_reason
        if reason is StopReason.INTERRUPTED:
            note = self._c(YELLOW, "[interrupted -- type to continue]")
        elif reason is StopReason.MAX_TURNS:
            note = self._c(YELLOW, "[stopped at the turn limit, work may be unfinished]")
        elif reason is StopReason.ERROR:
            note = self._c(RED, "[error]")
        else:
            note = self._c(GREEN, "[done]")

        usage = result.usage.total
        cost = self._c(DIM, f"{result.turns} turns · {result.tool_calls} tools · "
                            f"{usage.input_tokens + usage.output_tokens} tokens")
        self.out.write(f"\n{note} {cost}\n\n")
        self.out.flush()


def _brief(args) -> str:
    """One short line describing a call. The full arguments belong in the
    session file, not on screen -- a 40-line patch scrolls the conversation
    away exactly when the user wants to read it."""
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "pattern", "query"):
        if key in args:
            text = str(args[key]).replace("\n", " ")
            return text[:70] + ("…" if len(text) > 70 else "")
    return ""


def _format_markdown(text: str, colour: bool = True) -> str:
    """Small scrollback-safe subset; provider text remains plain-text safe."""
    rendered: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            rendered.append(f"    {line}")
        elif line.startswith("#"):
            rendered.append(line.lstrip("#").strip())
        elif line.startswith(("- ", "* ")):
            rendered.append(f"• {line[2:].replace('`', '')}")
        else:
            rendered.append(line.replace("`", ""))
    return "\n".join(rendered)


def run_repl(agent: Agent, interrupt: Interrupt,
             stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Prompt, run, repeat until EOF.

    The reader thread is started per turn rather than for the session: while
    the user is typing at the prompt, `input()` needs the terminal in its
    normal cooked mode -- line editing, echo, backspace. Raw mode belongs only
    to the window where the agent is working and the user is not typing.
    """
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    # The spinner only runs on a real terminal: in a pipe or a log file its
    # carriage returns are line noise.
    renderer = Renderer(out=out, colour=out.isatty(),
                        spinner=Spinner(out=out, enabled=out.isatty()))
    terminal_input = TerminalInput(stdin, out)

    # Every escape goes through the renderer's colour check. Writing them
    # directly -- as the banner and prompt once did -- leaks control codes
    # into redirected output, which is exactly the class of bug the bash
    # sanitiser exists to prevent.
    out.write(renderer._c(BOLD, BANNER_TEXT) + "\n\n")
    unsubscribe = agent.subscribe(renderer.on_event)

    try:
        while True:
            try:
                line = terminal_input.read(renderer._c(BOLD, "› ") if renderer.colour else "> ")
            except KeyboardInterrupt:  # pragma: no cover - prompt_toolkit handles this on TTY
                line = ""

            if line is None:                   # Ctrl-D
                out.write("\n")
                return 0
            prompt = line.strip()
            if not prompt:
                continue
            if prompt.startswith("!"):
                _run_direct_command(prompt[1:], agent, interrupt, stdin, renderer)
                continue
            if prompt == "/tree":
                _run_command(prompt, agent, renderer)
                choice = terminal_input.read("branch number/id (Enter to cancel): ")
                if choice and choice.strip():
                    _run_command(f"/rewind {choice.strip()}", agent, renderer)
                else:
                    renderer.status("[tree selection cancelled]")
                continue
            if prompt in ("exit", "quit") or _run_command(prompt, agent, renderer):
                return 0
            if prompt.startswith("/"):
                continue

            # Cleared per turn: a flag left set by the previous interrupt
            # would abort this run before it sent a single request, and
            # nothing on screen would explain why.
            interrupt.clear()

            in_flight = InFlightInput(agent, interrupt, renderer)
            with KeyReader(stream=stdin, interrupt=interrupt,
                           on_key=in_flight.on_key) as keys:
                agent.ask_user = lambda questions: _prompt_ask_user(
                    questions, terminal_input, renderer, keys, interrupt)
                try:
                    result = agent.run(prompt)
                    # Background settlements / reports may already be queued.
                    while True:
                        cont = agent.continue_queued()
                        if cont is None:
                            break
                        result = cont
                except Exception as exc:  # noqa: BLE001 - a crash must not eat the session
                    renderer._break_line()
                    out.write(renderer._c(RED, f"[{type(exc).__name__}: {exc}]") + "\n\n")
                    continue
                finally:
                    agent.ask_user = None
                    in_flight.close()

            renderer.finish(agent, result)
            if interrupt.count >= 2:
                renderer.status("[exiting after interrupt]")
                return 0
    finally:
        unsubscribe()


def _check_ask_interrupt(interrupt: Interrupt | None) -> None:
    if interrupt is not None and interrupt.is_set():
        raise AskUserError(
            "ask_user_question was aborted before the user answered",
            "ASK_ABORTED")


def _read_ask_line(
    terminal_input: TerminalInput,
    prompt: str,
    interrupt: Interrupt | None,
) -> str:
    _check_ask_interrupt(interrupt)
    try:
        raw = terminal_input.read(prompt, abortable=True)
    except KeyboardInterrupt:
        if interrupt is not None:
            interrupt.set()
        raise AskUserError(
            "ask_user_question was aborted before the user answered",
            "ASK_ABORTED") from None
    _check_ask_interrupt(interrupt)
    if raw is None:
        raise AskUserError(
            "the user cancelled ask_user_question", "ASK_CANCELLED")
    return raw


def _parse_ask_choice(
    q: AskQuestion,
    text: str,
    terminal_input: TerminalInput,
    interrupt: Interrupt | None,
) -> AskAnswerItem:
    text = text.strip()
    if q.multi_select and q.options:
        parts = [p for p in text.replace(",", " ").split() if p]
        if parts and all(p.isdigit() for p in parts) and "0" not in parts:
            selected: list[str] = []
            seen: set[str] = set()
            for part in parts:
                n = int(part)
                if not (1 <= n <= len(q.options)):
                    raise AskUserError(
                        f"invalid choice {part!r}; pick from 1-{len(q.options)} "
                        f"or 0 for custom",
                        "BAD_ANSWER")
                label = q.options[n - 1].label
                if label not in seen:
                    seen.add(label)
                    selected.append(label)
            if selected:
                return AskAnswerItem(id=q.id, selected=selected)
        if text == "0" or not parts:
            typed = _read_ask_line(terminal_input, "your answer> ", interrupt)
            return AskAnswerItem(id=q.id, selected=[], custom=typed.strip())
        # Non-numeric free text counts as custom for multi-select.
        return AskAnswerItem(id=q.id, selected=[], custom=text)

    # Single-select: one option number, 0 + typed text, or free text.
    parts = [p for p in text.replace(",", " ").split() if p]
    if q.options and len(parts) > 1 and all(p.isdigit() for p in parts):
        raise AskUserError(
            f"pick one option 1-{len(q.options)} (or 0 for custom); "
            f"got {text!r}",
            "BAD_ANSWER")
    if text.isdigit() and q.options:
        n = int(text)
        if 1 <= n <= len(q.options):
            return AskAnswerItem(id=q.id, selected=[q.options[n - 1].label])
        if text != "0":
            raise AskUserError(
                f"choice must be 1-{len(q.options)} or 0 for custom; "
                f"got {text!r}",
                "BAD_ANSWER")
    custom = text if text != "0" else ""
    if text == "0":
        typed = _read_ask_line(terminal_input, "your answer> ", interrupt)
        custom = typed.strip()
    return AskAnswerItem(id=q.id, selected=[], custom=custom or text)


def _prompt_ask_user(
    questions: list[AskQuestion],
    terminal_input: TerminalInput,
    renderer: Renderer,
    key_reader: KeyReader | None,
    interrupt: Interrupt | None = None,
) -> AskAnswer:
    """Show options, wait for a choice or free text, return structured answers.

    Pauses KeyReader so cooked line editing works; resumes afterward so ESC
    still interrupts the rest of the run. Escape / Ctrl-C / interrupt during
    the wait raises AskUserError(ASK_ABORTED); EOF raises ASK_CANCELLED.
    """
    # TOOL_START left the spinner running; it rewrites the line with \\r and
    # corrupts choice> typing until the tool ends. Stop it before we prompt.
    renderer._stop_spinner()
    if key_reader is not None:
        key_reader.pause()
    try:
        answers: list[AskAnswerItem] = []
        for q in questions:
            _check_ask_interrupt(interrupt)
            renderer.status("")
            if q.header:
                renderer.status(q.header)
            renderer.status(q.question)
            for i, opt in enumerate(q.options, start=1):
                extra = f" — {opt.description}" if opt.description else ""
                renderer.status(f"  {i}) {opt.label}{extra}")
            if q.multi_select:
                renderer.status(
                    "  (multi-select: enter numbers like 1,3 or 0 for custom)")
            else:
                renderer.status("  0) type your own answer")
            while True:
                raw = _read_ask_line(terminal_input, "choice> ", interrupt)
                try:
                    answers.append(
                        _parse_ask_choice(q, raw, terminal_input, interrupt))
                    break
                except AskUserError as exc:
                    if exc.code != "BAD_ANSWER":
                        raise
                    renderer.status(f"  ({exc})")
        return AskAnswer(answers=answers)
    finally:
        if key_reader is not None:
            key_reader.resume()


def _run_direct_command(command: str, agent: Agent, interrupt: Interrupt,
                        stdin: TextIO, renderer: Renderer) -> CommandResult | None:
    """Run `!command` outside the model transcript, like Pi's bash mode."""
    if not command.strip():
        renderer.status("[bash command cannot be empty]")
        return None

    renderer.status(f"$ {command}")
    interrupt.clear()

    def show_output(stream: str, text: str) -> None:
        renderer._break_line()
        if stream == "stderr":
            renderer.out.write(renderer._c(YELLOW, "[stderr] "))
        renderer.out.write(text)
        if text and not text.endswith("\n"):
            renderer.out.write("\n")
        renderer.out.flush()

    with KeyReader(stream=stdin, interrupt=interrupt):
        result = run_command(command, agent.policy, interrupt=interrupt, on_output=show_output)

    renderer._break_line()
    if result.interrupted:
        renderer.status("[bash interrupted]")
    elif result.timed_out:
        renderer.status("[bash timed out]")
    elif result.exit_code:
        renderer.status(f"[bash exited with code {result.exit_code}]")
    else:
        renderer.status("[bash done]")
    return result


def _run_command(prompt: str, agent: Agent, renderer: Renderer) -> bool:
    """Run a small, intentionally literal initial slash-command set.

    Returning ``True`` asks the REPL to exit. Unknown commands remain visible
    errors rather than becoming accidental model instructions.
    """
    if not prompt.startswith("/"):
        return False

    command, _, argument = prompt[1:].partition(" ")
    argument = argument.strip()
    if command in ("exit", "quit"):
        return True
    session_handled = _run_session_command(command, argument, agent, renderer)
    if session_handled is not None:
        return session_handled
    if command == "help":
        renderer.status(
            "commands: /help /session /tree /mark <label> /rewind <id|branch> "
            "/new /compact /expand <tool-id> /collapse /model <name> "
            "/thinking [off|minimal|low|medium|high|xhigh|max|hide|show] /exit")
    elif command == "expand":
        if not argument or not renderer.expand(argument):
            renderer.status("[usage: /expand <tool-id>]")
    elif command == "collapse":
        renderer.collapse()
    elif command == "model":
        if argument:
            agent.set_model(argument)
            renderer.status(f"[model: {argument}]")
        else:
            current = agent.model or getattr(agent.llm, "model", "default")
            renderer.status(f"[model: {current}; use /model <name> to change]")
    elif command == "thinking":
        _set_thinking_command(argument, agent, renderer)
    else:
        renderer.status(f"[unknown command: /{command}; use /help]")
    return False


def _set_thinking_command(argument: str, agent: Agent, renderer: Renderer) -> None:
    """Pi's thinking level control (Shift+Tab / --thinking), as a slash command.

    `hide` / `show` toggle display of streamed reasoning without changing the
    effort sent to the provider -- matching Pi's Ctrl+T hideThinkingBlock.
    """
    if not argument:
        level = agent.thinking_level
        label = level.value if level is not None else "off"
        visibility = "hidden" if renderer.hide_thinking else "shown"
        renderer.status(
            f"[thinking: {label}; display {visibility}; "
            f"use /thinking <level|hide|show>]")
        return
    key = argument.lower()
    if key == "hide":
        renderer.hide_thinking = True
        renderer.status("[thinking display: hidden]")
        return
    if key == "show":
        renderer.hide_thinking = False
        renderer.status("[thinking display: shown]")
        return
    try:
        level = ThinkingLevel(key)
    except ValueError:
        renderer.status(
            "[usage: /thinking off|minimal|low|medium|high|xhigh|max|hide|show]")
        return
    agent.set_thinking_level(level)
    renderer.status(f"[thinking: {level.value}]")


def _run_session_command(command: str, argument: str, agent: Agent,
                         renderer: Renderer) -> bool | None:
    """Session commands kept separate from generic REPL command dispatch."""
    try:
        if command == "session":
            renderer.status(agent.session_status())
        elif command == "tree":
            renderer.status(agent.session_tree())
        elif command == "mark":
            entry_id = agent.label(argument or "mark")
            renderer.status(f"[marked {entry_id[:8]}: {argument or 'mark'}]")
        elif command == "rewind":
            if not argument:
                renderer.status("[usage: /rewind <entry-id-prefix|branch-number>]")
            else:
                entry_id = agent.resolve_session_target(argument)
                agent.rewind_to(entry_id)
                renderer.status(f"[rewound to {entry_id[:8]}]")
        elif command == "new":
            path = agent.new_session()
            renderer.status(f"[started new conversation in session: {path}]")
        elif command == "compact":
            agent.compact_now()
            renderer.status("[conversation compacted]")
        else:
            return None
    except (KeyError, ValueError) as exc:
        renderer.status(f"[session: {exc}]")
    return False
