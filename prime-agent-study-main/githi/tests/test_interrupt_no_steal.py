"""The ESC reader must not eat the next prompt.

Reported from a real session: a prompt beginning "Clone django/django..."
reached the agent as " django/django...", so it never knew to clone and spent
every turn hunting for a directory that does not exist.

The cause is a thread blocked on input it can no longer be told to stop
reading. `_read_loop` sits inside `stream.read(1)`, which does not return
until a key arrives. Setting the stop flag on context exit does nothing --
the thread is *inside* the read and cannot see it. So it stays alive, holding
a claim on stdin, and when the user types their next prompt it consumes the
first character before noticing it was supposed to have finished.

The damage compounds: every turn leaves another blocked thread queued on
stdin, so by the fifth prompt five characters vanish. That matches losing a
whole word.

The fix is to poll with a timeout instead of blocking, so the thread checks
the flag regularly and exits without taking a character with it.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from githi.agent.interrupt import ESC, Interrupt, KeyReader


def _pipe():
    """A real blocking stream. `StringIO` returns immediately and so cannot
    exhibit the bug at all."""
    read_fd, write_fd = os.pipe()
    return os.fdopen(read_fd, "r"), os.fdopen(write_fd, "w")


class TestNoInputTheft:
    def test_a_stopped_reader_does_not_consume_the_next_keystroke(self):
        """The reported bug, reduced: stop the reader, then type."""
        reader_stream, writer = _pipe()
        try:
            reader = KeyReader(stream=reader_stream, interrupt=Interrupt())
            thread = threading.Thread(target=reader._read_loop, daemon=True)
            thread.start()
            time.sleep(0.05)

            reader._stop.set()
            thread.join(timeout=2.0)
            assert not thread.is_alive(), "the reader thread outlived its stop flag"

            writer.write("Clone django\n")
            writer.flush()
            assert reader_stream.readline() == "Clone django\n"
        finally:
            reader_stream.close()
            writer.close()

    def test_the_thread_exits_promptly_with_no_input_at_all(self):
        """It must not need a keystroke to notice it should stop -- waiting
        for one is what leaves it parked on stdin."""
        reader_stream, writer = _pipe()
        try:
            reader = KeyReader(stream=reader_stream, interrupt=Interrupt())
            thread = threading.Thread(target=reader._read_loop, daemon=True)
            thread.start()
            time.sleep(0.05)

            reader._stop.set()
            thread.join(timeout=2.0)
            assert not thread.is_alive()
        finally:
            reader_stream.close()
            writer.close()

    def test_repeated_cycles_leave_no_threads_behind(self):
        """Each turn starts a reader. Leaking one per turn is what turns a
        single lost character into a lost word."""
        before = threading.active_count()
        reader_stream, writer = _pipe()
        try:
            for _ in range(5):
                reader = KeyReader(stream=reader_stream, interrupt=Interrupt())
                thread = threading.Thread(target=reader._read_loop, daemon=True)
                thread.start()
                time.sleep(0.03)
                reader._stop.set()
                thread.join(timeout=2.0)
            time.sleep(0.05)
            assert threading.active_count() <= before + 1
        finally:
            reader_stream.close()
            writer.close()


class TestEscStillWorks:
    def test_esc_is_still_detected(self):
        """The fix must not cost the feature it is protecting."""
        reader_stream, writer = _pipe()
        flag = Interrupt()
        try:
            reader = KeyReader(stream=reader_stream, interrupt=flag)
            thread = threading.Thread(target=reader._read_loop, daemon=True)
            thread.start()

            writer.write(ESC)
            writer.flush()
            flag.wait(timeout=2.0)
            assert flag.is_set()
        finally:
            reader._stop.set()
            reader_stream.close()
            writer.close()

    def test_ordinary_keys_do_not_interrupt(self):
        reader_stream, writer = _pipe()
        flag = Interrupt()
        try:
            reader = KeyReader(stream=reader_stream, interrupt=flag)
            thread = threading.Thread(target=reader._read_loop, daemon=True)
            thread.start()

            writer.write("abc")
            writer.flush()
            time.sleep(0.2)
            assert not flag.is_set()
        finally:
            reader._stop.set()
            reader_stream.close()
            writer.close()
