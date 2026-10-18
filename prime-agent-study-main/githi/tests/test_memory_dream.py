"""Consolidation — the "dream" pass (ports of `dream.rs` and `dream_lock.rs`).

Periodically, an auxiliary model reads recent session logs and rewrites
`MEMORY.md`: merging related facts, resolving contradictions, converting
relative dates to absolute, discarding ephemera. It is what turns a pile of
transcripts into curated knowledge.

Three details in Grok's implementation are load-bearing and easy to get
wrong, so they get the most tests here:

* **Only sessions actually read are deleted.** Input is capped; sessions
  beyond the cap must survive for the next pass rather than being destroyed
  unread.
* **The lock reclaims itself.** A crashed run leaves a lock file; if a dead
  PID could block consolidation forever, memory silently stops improving and
  nothing reports it.
* **Bad output is rejected, not written.** A model replying with prose
  instead of a document would otherwise overwrite curated memory with chat.
"""

from __future__ import annotations

import os
import time

import pytest

from githi.memory.dream import (
    MAX_DREAM_INPUT_CHARS,
    DreamConfig,
    DreamGate,
    DreamStatus,
    build_dream_message,
    check_dream_gates,
    process_dream_response,
    run_dream,
)
from githi.memory.dream_lock import DreamLock, sessions_since
from githi.memory.storage import MemoryScope, MemoryStorage


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(home=tmp_path / "home", workspace=tmp_path / "proj")


def _sessions(storage, count: int, body: str = "log body ") -> None:
    for i in range(count):
        storage.append_session(f"task-{i}", f"# Session {i}\n\n{body}{i}\n",
                               run_id=f"run{i:05d}")


class TestGates:
    def test_disabled_config_never_fires(self, storage, tmp_path):
        _sessions(storage, 5)
        gate = check_dream_gates(DreamConfig(enabled=False),
                                 DreamLock(tmp_path), storage.sessions_dir())
        assert gate.status is DreamGate.DISABLED

    def test_too_soon_since_last_consolidation(self, storage, tmp_path):
        _sessions(storage, 5)
        lock = DreamLock(tmp_path)
        lock.mark_consolidated()
        gate = check_dream_gates(DreamConfig(min_hours=24), lock, storage.sessions_dir())
        assert gate.status is DreamGate.TOO_SOON

    def test_too_few_sessions(self, storage, tmp_path):
        _sessions(storage, 1)
        gate = check_dream_gates(DreamConfig(min_hours=0, min_sessions=5),
                                 DreamLock(tmp_path), storage.sessions_dir())
        assert gate.status is DreamGate.TOO_FEW_SESSIONS

    def test_open_when_all_gates_pass(self, storage, tmp_path):
        _sessions(storage, 4)
        gate = check_dream_gates(DreamConfig(min_hours=0, min_sessions=2),
                                 DreamLock(tmp_path), storage.sessions_dir())
        assert gate.status is DreamGate.OPEN and len(gate.sessions) == 4

    def test_gates_are_checked_cheapest_first(self, storage, tmp_path):
        """Grok's documented order: config, then time, then a directory
        listing. Checking `enabled` last would stat the filesystem on every
        turn for a feature that is switched off."""
        gate = check_dream_gates(DreamConfig(enabled=False, min_hours=999),
                                 DreamLock(tmp_path), storage.sessions_dir())
        assert gate.status is DreamGate.DISABLED, "time gate ran before config gate"

    def test_missing_sessions_dir_is_not_an_error(self, storage, tmp_path):
        gate = check_dream_gates(DreamConfig(min_hours=0, min_sessions=1),
                                 DreamLock(tmp_path), storage.sessions_dir())
        assert gate.status is DreamGate.TOO_FEW_SESSIONS

    def test_current_session_is_excluded(self, storage, tmp_path):
        """The in-flight run is still being written; consolidating and
        deleting it would destroy the transcript underneath itself."""
        _sessions(storage, 3)
        current = storage.list_sessions()[0].stem
        gate = check_dream_gates(DreamConfig(min_hours=0, min_sessions=1),
                                 DreamLock(tmp_path), storage.sessions_dir(),
                                 current_session_stem=current)
        assert current not in gate.sessions


class TestLock:
    def test_acquire_then_release(self, tmp_path):
        lock = DreamLock(tmp_path)
        assert lock.try_acquire() is True
        lock.release()
        assert lock.try_acquire() is True

    def test_second_acquire_by_a_live_holder_fails(self, tmp_path):
        lock = DreamLock(tmp_path)
        assert lock.try_acquire() is True
        assert DreamLock(tmp_path).try_acquire() is False

    def test_stale_lock_from_a_dead_pid_is_reclaimed(self, tmp_path):
        """The failure this prevents: a crashed run leaves a lock, and
        consolidation silently never runs again."""
        (tmp_path / ".dream-lock").write_text("999999")   # PID that cannot exist
        assert DreamLock(tmp_path).try_acquire() is True

    def test_lock_older_than_the_staleness_window_is_reclaimed(self, tmp_path):
        lock_file = tmp_path / ".dream-lock"
        lock_file.write_text(str(os.getpid()))            # a *live* pid
        old = time.time() - 7200
        os.utime(lock_file, (old, old))
        assert DreamLock(tmp_path, stale_seconds=3600).try_acquire() is True

    def test_corrupt_lock_file_is_reclaimed(self, tmp_path):
        (tmp_path / ".dream-lock").write_text("not-a-pid")
        assert DreamLock(tmp_path).try_acquire() is True

    def test_last_consolidated_at_is_none_initially(self, tmp_path):
        assert DreamLock(tmp_path).last_consolidated_at() is None

    def test_mark_consolidated_records_a_timestamp(self, tmp_path):
        lock = DreamLock(tmp_path)
        lock.mark_consolidated()
        assert lock.last_consolidated_at() is not None


class TestSessionsSince:
    def test_counts_only_newer_sessions(self, storage):
        """mtimes are set explicitly: filesystem timestamp resolution is
        coarse enough that a real sleep makes this flaky."""
        _sessions(storage, 2)
        storage.append_session("later", "# Later\n\nbody\n", run_id="zzzzzzzz")

        cutoff = time.time()
        for path in storage.list_sessions():
            when = cutoff + 60 if "later" in path.name else cutoff - 60
            os.utime(path, (when, when))

        newer = sessions_since(storage.sessions_dir(), cutoff)
        assert len(newer) == 1 and "later" in newer[0]

    def test_all_sessions_when_never_consolidated(self, storage):
        _sessions(storage, 3)
        assert len(sessions_since(storage.sessions_dir(), 0)) == 3

    def test_missing_directory_returns_empty(self, tmp_path):
        assert sessions_since(tmp_path / "absent", 0) == []


class TestMessageBuilding:
    def test_includes_session_content(self, storage):
        _sessions(storage, 2)
        message = build_dream_message(storage, [p.stem for p in storage.list_sessions()])
        assert "log body 0" in message.content

    def test_prepends_existing_memory_for_merging(self, storage):
        """Consolidation must *merge*, not replace -- without prior memory in
        the prompt the model silently discards everything learned before."""
        storage.write(MemoryScope.WORKSPACE, "# Prior\n\nAn established fact.\n")
        _sessions(storage, 1)
        message = build_dream_message(storage, [p.stem for p in storage.list_sessions()])
        assert "An established fact" in message.content

    def test_scaffold_memory_is_not_treated_as_prior_knowledge(self, storage):
        storage.write(MemoryScope.WORKSPACE,
                      "# Memory\n\nAdd project-specific knowledge here.\n")
        _sessions(storage, 1)
        message = build_dream_message(storage, [p.stem for p in storage.list_sessions()])
        assert "Add project-specific knowledge here" not in message.content

    def test_input_is_capped(self, storage):
        _sessions(storage, 40, body="x" * 4000)
        message = build_dream_message(storage, [p.stem for p in storage.list_sessions()])
        assert len(message.content) <= MAX_DREAM_INPUT_CHARS * 1.2

    def test_only_sessions_actually_read_are_reported(self, storage):
        """The detail that prevents silent data loss: sessions past the cap
        were never seen by the model, so they must not be deleted."""
        _sessions(storage, 40, body="x" * 4000)
        stems = [p.stem for p in storage.list_sessions()]
        message = build_dream_message(storage, stems)
        assert 0 < len(message.processed_stems) < len(stems)


class TestResponseProcessing:
    def test_accepts_a_markdown_document(self):
        assert process_dream_response("## Topic\n\nA durable fact.\n") is not None

    def test_rejects_no_reply(self):
        """The model's way of saying nothing was worth keeping. Writing the
        literal string 'NO_REPLY' into MEMORY.md would be absurd."""
        assert process_dream_response("NO_REPLY") is None
        assert process_dream_response("no reply") is None

    def test_rejects_prose_without_headers(self):
        """Structure is the contract. Unstructured prose is usually
        commentary, and writing it overwrites curated memory with chat."""
        assert process_dream_response("Sure! Here's what I found in the logs.") is None

    def test_rejects_empty(self):
        assert process_dream_response("   ") is None


class TestRunDream:
    def test_skips_when_gates_are_closed(self, storage, tmp_path):
        result = run_dream(storage, DreamConfig(enabled=False),
                           lambda system, user: "## X\n\ny\n", lock_dir=tmp_path)
        assert result.status is DreamStatus.SKIPPED

    def test_writes_consolidated_memory(self, storage, tmp_path):
        _sessions(storage, 3)
        result = run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                           lambda system, user: "## Learned\n\nA durable fact.\n",
                           lock_dir=tmp_path)
        assert result.status is DreamStatus.COMPLETED
        assert "A durable fact" in storage.read(MemoryScope.WORKSPACE)

    def test_consumed_sessions_are_deleted(self, storage, tmp_path):
        _sessions(storage, 3)
        run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                  lambda system, user: "## L\n\nfact\n", lock_dir=tmp_path)
        assert storage.list_sessions() == []

    def test_no_reply_leaves_memory_and_sessions_untouched(self, storage, tmp_path):
        """Nothing worth keeping must not mean 'destroy the evidence'."""
        storage.write(MemoryScope.WORKSPACE, "# Prior\n\nkeep me\n")
        _sessions(storage, 3)
        result = run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                           lambda system, user: "NO_REPLY", lock_dir=tmp_path)
        assert result.status is DreamStatus.NOTHING_TO_CONSOLIDATE
        assert "keep me" in storage.read(MemoryScope.WORKSPACE)
        assert len(storage.list_sessions()) == 3

    def test_model_failure_is_reported_not_raised(self, storage, tmp_path):
        _sessions(storage, 3)

        def broken(system, user):
            raise RuntimeError("model unavailable")

        result = run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                           broken, lock_dir=tmp_path)
        assert result.status is DreamStatus.FAILED
        assert len(storage.list_sessions()) == 3, "sessions destroyed on failure"

    def test_lock_is_released_after_a_run(self, storage, tmp_path):
        _sessions(storage, 3)
        run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                  lambda system, user: "## L\n\nfact\n", lock_dir=tmp_path)
        assert DreamLock(tmp_path).try_acquire() is True

    def test_lock_is_released_even_when_the_model_fails(self, storage, tmp_path):
        """A held lock after a crash blocks all future consolidation."""
        _sessions(storage, 3)

        def broken(system, user):
            raise RuntimeError("boom")

        run_dream(storage, DreamConfig(min_hours=0, min_sessions=2), broken,
                  lock_dir=tmp_path)
        assert DreamLock(tmp_path).try_acquire() is True

    def test_a_concurrent_dream_is_refused(self, storage, tmp_path):
        _sessions(storage, 3)
        DreamLock(tmp_path).try_acquire()
        result = run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                           lambda system, user: "## L\n\nfact\n", lock_dir=tmp_path)
        assert result.status is DreamStatus.SKIPPED

    def test_uncapped_sessions_survive_for_the_next_pass(self, storage, tmp_path):
        _sessions(storage, 40, body="x" * 4000)
        run_dream(storage, DreamConfig(min_hours=0, min_sessions=2),
                  lambda system, user: "## L\n\nfact\n", lock_dir=tmp_path)
        assert storage.list_sessions(), "deleted sessions the model never read"
