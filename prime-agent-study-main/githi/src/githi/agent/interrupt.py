"""ESC while the agent is working.

A `threading.Event` the agent polls, set by a daemon thread reading stdin in
raw mode. Chosen over asyncio deliberately: the agent core stays synchronous,
so nothing that already works has to be rewritten to gain the feature. The
cost is that the interrupt is *cooperative* -- it takes effect at the next
poll rather than instantly -- which is why the loop polls between stream
chunks and between tools, the two places where time is actually spent.

Raw mode is the fiddly part, and both guards here matter:

* **Not every stdin is a terminal.** Under a pipe, in CI, or in this test
  suite there is no raw mode to enter. That must degrade quietly rather than
  raise, or the same binary stops working outside an interactive shell.
* **The terminal must be restored even when the body raises.** A crash that
  leaves the terminal in raw mode leaves the *user's shell* unusable long
  after githi has exited -- no echo, no line editing -- and they have no
  reason to connect that to us.
"""

from __future__ import annotations

import threading
from typing import Any, TextIO

ESC = "\x1b"


class Interrupt:
    """A thread-safe stop flag with a count.

    The count exists so a caller can escalate: one ESC means "stop what you
    are doing", a second in quick succession means "I meant it" and may exit
    outright, matching what people already expect from Ctrl-C.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._count = 0

    def set(self) -> None:
        with self._lock:
            self._count += 1
        self._event.set()

    def clear(self) -> None:
        """Reset before each run. A flag left set from the previous run would
        abort the next one instantly, with nothing on screen to explain it."""
        with self._lock:
            self._count = 0
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


class KeyReader:
    """Watch stdin for ESC while the agent runs.

    A context manager, because the terminal mode it changes is global state
    that must be handed back whatever happens inside.
    """

    def __init__(self, stream: TextIO | None = None,
                 interrupt: Interrupt | None = None,
                 on_key: Any = None) -> None:
        import sys

        self.stream = stream if stream is not None else sys.stdin
        self.interrupt = interrupt or Interrupt()
        self.on_key = on_key
        self.active = False
        self._saved: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> KeyReader:
        if self._enter_raw_mode():
            self.active = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        # Always stop the reader and restore the terminal — even when we never
        # entered raw mode (pipes/CI). Leaving cbreak up after a crash makes
        # the user's shell unusable.
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._restore()
        self.active = False
        return False              # never swallow the exception

    def pause(self) -> None:
        """Leave raw mode so the user can type a normal answer.

        Needed while a tool like `ask_user_question` prompts during a run:
        KeyReader owns cbreak mode and would otherwise steal keystrokes.
        Safe to call when already paused or when stdin is not a TTY.
        """
        if not self.active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._restore()
        self.active = False

    def resume(self) -> None:
        """Watch ESC again after the user finished answering."""
        if self.active:
            return
        # The previous pause() left `_stop` set; a fresh Event is required or
        # the reader thread would exit immediately.
        self._stop = threading.Event()
        if self._enter_raw_mode():
            self.active = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

    def _enter_raw_mode(self) -> bool:
        try:
            import termios
            import tty

            if not self.stream.isatty():
                return False
            fd = self.stream.fileno()
            self._saved = termios.tcgetattr(fd)
            # cbreak, not full raw: keeps signal handling, so Ctrl-C still
            # works as the escape hatch if anything here misbehaves.
            tty.setcbreak(fd)
            return True
        except Exception:  # noqa: BLE001 - no TTY, no termios (Windows), no fd
            return False

    def _restore(self) -> None:
        if self._saved is None:
            return
        try:
            import termios

            termios.tcsetattr(self.stream.fileno(), termios.TCSADRAIN, self._saved)
        except Exception:  # noqa: BLE001 - nothing useful to do while unwinding
            pass
        finally:
            self._saved = None

    POLL_SECONDS = 0.1

    def _read_loop(self) -> None:
        """Poll for a key rather than blocking on one.

        A plain `read(1)` blocks until something arrives, so a thread sitting
        in it cannot see the stop flag and does not exit when the agent
        finishes its turn. It stays parked on stdin, and the next thing typed
        -- the user's next prompt -- is what finally wakes it, consuming the
        first character on the way out. One character per turn, compounding,
        because every turn leaves another thread queued.

        `select` with a timeout means the thread checks the flag ten times a
        second and can leave without taking anything with it.
        """
        import select

        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self.stream], [], [],
                                            self.POLL_SECONDS)
            except Exception:  # noqa: BLE001 - stream closed under us
                return
            if not ready:
                continue                  # nothing typed; re-check the flag

            try:
                key = self.stream.read(1)
            except Exception:  # noqa: BLE001 - stdin closed under us
                return
            if not key:
                return
            self._handle(key)

    def _handle(self, key: str) -> None:
        handled = False
        if self.on_key:
            handled = bool(self.on_key(key))
        if key == ESC and not handled:
            self.interrupt.set()
