"""Shared helpers for independent D7 memory acceptance (supervisor plane)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from supervisor.catalog.store import memory_profile_from_dict
from supervisor.catalog.types import MemoryProfile
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.seams.context_memory import CompactionPreparation

OUTER_REPO = Path(__file__).resolve().parents[5]
AI_REPO = Path(__file__).resolve().parents[4]
HARNESS_PROFILES = AI_REPO / "harness" / "memory_profiles"
GATEWAY_PORT = 8090  # model-gateway default; Memory must not collide


@pytest.fixture
def default_memory_profile() -> MemoryProfile:
    path = HARNESS_PROFILES / "default.json"
    assert path.is_file(), f"missing shipped default MemoryProfile at {path}"
    return memory_profile_from_dict(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
async def journal(tmp_path: Path) -> AsyncIterator[tuple[JournalWriter, JournalReader]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd7.db'}") as (_engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("d7-sess", runtime="echo")
        yield w, r


def compaction_prep(*, tokens_before: int = 95_000) -> CompactionPreparation:
    return CompactionPreparation(
        first_kept_entry_id="entry-1",
        messages_to_summarize=[{"role": "user", "content": "d7 long history"}],
        tokens_before=tokens_before,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )


def offered_memory_profiles() -> list[dict[str, Any]]:
    """Load all shipped harness MemoryProfile JSON bodies."""
    if not HARNESS_PROFILES.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(HARNESS_PROFILES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path.relative_to(AI_REPO))
        out.append(data)
    return out
