"""No credential is journalled: a tool that printed ``env`` or read a key file, or a model
that repeated a token, used to put it in the journal, its backups and every client."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from supervisor.agent.events import HandLost, MessageEnd, RunEnd, ToolEnd, ToolStart
from supervisor.db.models import EventRow
from supervisor.journal import snapshots
from supervisor.journal.partials import PartialOutputs, PartialSink
from supervisor.journal.reader import JournalReader
from supervisor.journal.secrets import mask, mask_bytes, mask_event, mask_text
from supervisor.journal.writer import JournalWriter
from tests.support.journal_db import Factory

OPENROUTER = "sk-or-v1-" + "0a1b2c3d" * 8
GITHUB = "ghp_" + "A1b2C3d4E5" * 4

MASKED = [
    (f"OPENROUTER_API_KEY={OPENROUTER}\n", "OPENROUTER_API_KEY=[redacted:openrouter_key]\n"),
    (f"remote: https://x:{GITHUB}@github.com", "remote: https://x:[redacted:github_token]@github.com"),
    ("sk-ant-api03-" + "x9" * 20, "[redacted:anthropic_key]"),
    ("sk-proj-" + "Ab1" * 15, "[redacted:openai_key]"),
    ("OPENAI_API_KEY=vk_" + "k3" * 20, "OPENAI_API_KEY=[redacted:virtual_key]"),
    ("id AKIAIOSFODNN7EXAMPLE ok", "id [redacted:aws_access_key] ok"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbn\n-----END OPENSSH PRIVATE KEY-----\n", "[redacted:private_key]\n"),
    ("Authorization: Bearer abc.def-ghi_jkl123", "Authorization: Bearer [redacted:bearer]"),
    ("DATABASE_PASSWORD='hunter2hunter2'", "DATABASE_PASSWORD='[redacted:secret]'"),
    ('{"client_secret": "a8f3k2m9x7q1w5"}', '{"client_secret": "[redacted:secret]"}'),
    ("t eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "t [redacted:jwt]"),
]

KEPT = [
    "token = request.headers['Authorization']",
    "PASSWORD=os.environ['PASSWORD']",
    "MAX_TOKENS=100000000",
    "def get_secret(self): return self._secret",
    "OPENAI_API_KEY=$OPENAI_API_KEY",
    "the API key goes in .env as OPENAI_API_KEY=<your key>",
    "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "commit 3f2a9c1e7b4d8a6f0e5c2b1a9d8e7f6a5b4c3d2e",
]


@pytest.mark.parametrize(("text", "expected"), MASKED)
def test_credentials_are_masked(text: str, expected: str) -> None:
    assert mask_text(text) == expected
    assert mask_text(expected) == expected, "masking twice changes nothing"


@pytest.mark.parametrize("text", KEPT)
def test_code_and_prose_that_name_credentials_are_kept(text: str) -> None:
    assert mask_text(text) == text


def test_nothing_masked_is_the_same_object() -> None:
    value = {"a": ["plain", {"b": "text"}], "n": 1}
    assert mask(value) is value
    event = ToolStart(tool_call_id="c", tool_name="read", args={"path": "a.txt"})
    assert mask_event(event) is event
    leaked = ToolStart(tool_call_id="c", tool_name="bash", args={"cmd": f"curl -H 'Authorization: Bearer {OPENROUTER}'"})
    assert OPENROUTER not in str(mask_event(leaked))


@pytest.mark.asyncio
async def test_the_journal_keeps_no_credential(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    printed = f"HOME=/root\nOPENROUTER_API_KEY={OPENROUTER}\n"
    await writer.append("s1", ToolEnd(tool_call_id="c", tool_name="bash", result={"stdout": printed}), turn_id="r", trace_id=None)
    said = [{"type": "text", "text": f"your token is {GITHUB}"}]
    await writer.append("s1", MessageEnd(role="assistant", content=said), turn_id="r", trace_id=None)
    first = [{"role": "user", "content": f"use {GITHUB}"}]
    await writer.append("s1", RunEnd(messages=first), turn_id="r", trace_id=None)
    await writer.append("s1", RunEnd(messages=[*first, {"role": "user", "content": "next"}]), turn_id="r2", trace_id=None)

    async with factory() as db:
        stored = " ".join(str(p) for p in (await db.execute(select(EventRow.payload))).scalars())
    assert OPENROUTER not in stored and GITHUB not in stored
    assert "[redacted:openrouter_key]" in stored and "HOME=/root" in stored
    events = await reader.read("s1")
    assert all(OPENROUTER not in str(e.payload) and GITHUB not in str(e.payload) for e in events)
    async with factory() as db:
        run_ends = (await db.execute(select(EventRow.payload).where(EventRow.type == "RunEnd").order_by(EventRow.seq))).scalars().all()
    assert snapshots.is_delta(run_ends[1]), "a conversation with a credential in it still stores as a delta"
    assert await reader.latest_history("s1") == [
        {"role": "user", "content": "use [redacted:github_token]"},
        {"role": "user", "content": "next"},
    ]


def test_a_key_used_as_a_field_name_is_masked() -> None:
    assert mask({OPENROUTER: "x", "plain": "y"}) == {"[redacted:openrouter_key]": "x", "plain": "y"}


def test_logs_are_masked_and_other_bytes_kept() -> None:
    logs = b"\xff\xfe binary\nOPENROUTER_API_KEY=" + OPENROUTER.encode() + b"\nexit 137\n"
    masked = mask_bytes(logs)
    assert OPENROUTER.encode() not in masked
    assert masked.startswith(b"\xff\xfe binary\n") and masked.endswith(b"\nexit 137\n")
    assert mask_bytes(b"\x00\x01 nothing here") == b"\x00\x01 nothing here"


@pytest.mark.asyncio
async def test_a_reply_streamed_a_few_characters_at_a_time_never_shows_a_key(factory: Factory) -> None:
    store = PartialOutputs(factory)
    writer = JournalWriter(factory)
    await writer.ensure_session("s1")
    sink = PartialSink(store, "s1", "r1", 0)
    reply = f"Here is the key you asked for: {OPENROUTER} and that is all."
    shown: list[str] = []
    for i in range(0, len(reply), 3):
        sink.on_delta("text", reply[i : i + 3])
        await sink._flush()
        partial = await store.get("s1")
        shown.append(partial.text if partial else "")
    assert all(not any(OPENROUTER[:n] in s for n in range(12, len(OPENROUTER) + 1)) for s in shown), "never even part of it"
    assert "[redacted:openrouter_key]" in shown[-1] and shown[-1].startswith("Here is the key")


@pytest.mark.asyncio
async def test_saved_logs_are_masked_when_read_even_if_stored_before(factory: Factory, tmp_path: Path) -> None:
    writer, reader = JournalWriter(factory, blob_dir=tmp_path), JournalReader(factory, blob_dir=tmp_path)
    await writer.ensure_session("s1")
    ref = await writer.put_blob(f"TOKEN={GITHUB}\n".encode())  # as an older build saved them
    assert ref is not None
    await writer.append("s1", HandLost(hand_id="h", provider="docker", reason="gone", logs_ref=ref), turn_id=None, trace_id=None)
    assert await reader.hand_logs("s1", ref) == b"TOKEN=[redacted:github_token]\n"
