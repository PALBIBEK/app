"""BH9: what a lost hand's provider recorded is kept, so nobody needs a shell into it.

Docker is not available everywhere the suite runs, so the container here is a
stand-in with the Docker SDK's shape (``reload``, ``attrs``, ``logs``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.sandbox.base import SandboxInfo
from supervisor.sandbox.providers.local_docker import LocalDockerSandbox


class Container:
    id = "0123456789abcdef"
    attrs: ClassVar[dict[str, Any]] = {
        "State": {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": True,
            "Error": "",
            "FinishedAt": "t",
        }
    }

    def reload(self) -> None:
        pass

    def logs(self, **_: Any) -> bytes:
        return b"x" * 10_000 + b"\nKilled\n"


def _sandbox() -> LocalDockerSandbox:
    return LocalDockerSandbox(
        Container(), client=None, info=SandboxInfo(id="sbx", provider="local_docker")
    )


async def test_a_containers_state_and_output_tail_are_its_logs() -> None:
    log = await _sandbox().logs(limit_bytes=500)
    assert log is not None and len(log) <= 500
    head, _, tail = log.partition(b"\n")
    assert b"exit_code=137" in head and b"oom_killed=True" in head  # why, before what it printed
    assert tail.endswith(b"Killed\n")  # the last of the output is what is kept


async def test_a_destroyed_container_has_none() -> None:
    sandbox = _sandbox()
    sandbox._destroyed = True
    assert await sandbox.logs() is None


def test_blobs_are_stored_by_content_and_read_back_only_by_their_ref(tmp_path: Path) -> None:
    writer = JournalWriter(None, blob_dir=tmp_path)  # type: ignore[arg-type]
    reader = JournalReader(None, blob_dir=tmp_path)  # type: ignore[arg-type]
    ref = writer.put_blob(b"exit_code=137")
    assert ref is not None and writer.put_blob(b"exit_code=137") == ref
    assert reader.read_blob(ref) == b"exit_code=137"
    for bad in ("../etc/passwd", "a" * 63, ref.upper()):
        try:
            reader.read_blob(bad)
        except FileNotFoundError:
            continue
        raise AssertionError(f"read {bad!r}")
    assert JournalWriter(None).put_blob(b"x") is None  # type: ignore[arg-type]
