"""Large journal payloads are readable from every replica and worker: they live in the
platform's shared object storage, not on the disk of the process that wrote them."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import HandLost, RunEnd, ToolEnd
from supervisor.bootstrap import journal_blobs
from supervisor.journal.blobs import BLOB_PREFIX, LocalBlobs, ObjectBlobs, blob_ref
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.session.erase import SessionEraser
from tests.support.journal_db import Factory

BIG = "x" * 100_000  # past the 64 KiB a row keeps


def _big(i: int = 0) -> ToolEnd:
    return ToolEnd(tool_call_id=f"c{i}", tool_name="read", result={"ok": True, "payload": BIG})


@pytest.mark.asyncio
async def test_what_one_worker_offloads_every_other_process_reads(factory: Factory) -> None:
    bucket = MemoryObjectStorage()  # the one store every process is configured with
    worker = JournalWriter(factory, blobs=ObjectBlobs(bucket))
    api = JournalReader(factory, blobs=ObjectBlobs(bucket))  # another process, another disk
    await worker.ensure_session("s1")
    await worker.append("s1", _big(), turn_id="r1", trace_id=None)
    await worker.append("s1", RunEnd(messages=[{"role": "tool", "content": BIG}]), turn_id="r1", trace_id=None)
    events = await api.read("s1")
    assert [e.payload for e in events] == [_big(), RunEnd(messages=[{"role": "tool", "content": BIG}])]
    assert await api.latest_history("s1") == [{"role": "tool", "content": BIG}]
    assert sorted(k for k in bucket._objects if k.startswith(BLOB_PREFIX))  # kept in the bucket


@pytest.mark.asyncio
async def test_a_directory_per_process_is_what_failed(factory: Factory, tmp_path: Path) -> None:
    """Why a deployment must not use one: a payload a worker offloads to its own disk
    makes the session unreadable to every other process."""
    worker = JournalWriter(factory, blob_dir=tmp_path / "worker")
    api = JournalReader(factory, blob_dir=tmp_path / "api")
    await worker.ensure_session("s1")
    await worker.append("s1", _big(), turn_id="r1", trace_id=None)
    with pytest.raises(FileNotFoundError):
        await api.read("s1")


@pytest.mark.asyncio
async def test_lost_sandbox_logs_and_erasing_use_the_shared_store(factory: Factory) -> None:
    bucket = MemoryObjectStorage()
    blobs = ObjectBlobs(bucket)
    writer, reader = JournalWriter(factory, blobs=blobs), JournalReader(factory, blobs=ObjectBlobs(bucket))
    await writer.ensure_session("s1", tenant_id="t1")
    logs = await writer.put_blob(b"exit_code=137")
    assert logs == blob_ref(b"exit_code=137")
    await writer.append("s1", HandLost(hand_id="h", provider="docker", reason="gone", logs_ref=logs), turn_id=None, trace_id=None)
    await writer.append("s1", _big(), turn_id="r1", trace_id=None)
    assert await reader.hand_logs("s1", logs) == b"exit_code=137"
    erased = await SessionEraser(factory, blobs=ObjectBlobs(bucket), objects=None).erase("s1")
    assert erased.payloads == 2
    assert not [k for k in bucket._objects if k.startswith(BLOB_PREFIX)], "erasing removes them from the bucket"


@pytest.mark.asyncio
async def test_blob_refs_are_checked_before_any_lookup(tmp_path: Path) -> None:
    for store in (ObjectBlobs(MemoryObjectStorage()), LocalBlobs(tmp_path)):
        for bad in ("../../etc/passwd", "a" * 63, "A" * 64):
            with pytest.raises(FileNotFoundError):
                await store.get(bad)
        ref = blob_ref(b"data")
        await store.put(ref, b"data")
        await store.put(ref, b"data")  # the same bytes, kept once
        assert await store.get(ref) == b"data"
        assert await store.delete(ref) and not await store.delete(ref)


def test_a_deployment_keeps_them_in_its_shared_object_storage(tmp_path: Path) -> None:
    class Bucket:  # any real store (S3, SeaweedFS): not the per-process memory one
        pass

    assert isinstance(journal_blobs(Bucket(), tmp_path), ObjectBlobs)  # type: ignore[arg-type]
    assert isinstance(journal_blobs(MemoryObjectStorage(), tmp_path), LocalBlobs)
