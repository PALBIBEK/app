"""Append-only event journal for session durability.

The journal provides:
- Durable event persistence with atomic sequence allocation
- Crash recovery via unpaired event detection
- Large payload offload to blob storage
- HTTP command deduplication

Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │                     JournalWriter                        │
    │   append() / append_batch() → allocate seq → persist    │
    └─────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────┐
    │                      EventRow                            │
    │   (session_id, seq, type, payload, ts_ms, trace_id)     │
    └─────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────┐
    │                     JournalReader                        │
    │   read() / unpaired_tool_starts() / latest_history()    │
    └─────────────────────────────────────────────────────────┘

Example usage:
    from supervisor.journal import JournalWriter, JournalReader

    writer = JournalWriter(session_factory)
    await writer.ensure_session(session_id)
    seq = await writer.append(session_id, event, turn_id=tid, trace_id=trace)

    reader = JournalReader(session_factory)
    envelopes = await reader.read(session_id, since=0)
    unpaired = await reader.unpaired_tool_starts(session_id)
"""

from supervisor.journal.commands import CommandStore
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import (
    DeserializationError,
    SerializationError,
    decode_event,
    encode_event,
    event_type_name,
)
from supervisor.journal.writer import JournalWriter, OversizedPayloadError

__all__ = [
    "CommandStore",
    "DeserializationError",
    "JournalReader",
    "JournalWriter",
    "OversizedPayloadError",
    "SerializationError",
    "decode_event",
    "encode_event",
    "event_type_name",
]
