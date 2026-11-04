"""The session journal: every event a session has, in order, kept for as long as it lives.

- ``writer``: appends (seq allocated under the session row's lock; keyed writes land once;
  credentials masked; large payloads in the blob store; a run's conversation as a delta).
- ``reader``: reads in order, by run, by type, the latest of a type, with deltas resolved.
- ``serde``: events to plain JSON and back; types this build does not know pass through.
- ``snapshots``, ``blobs``, ``secrets``, ``index``: how a run's conversation, large
  payloads, credentials and a session's index row are kept.
- ``watch``: wakes event streams when their session changed (one shared poll a process).
- ``partials``: the reply being written, outside the journal.
- ``scrub``: masks credentials stored before the journal masked them.
- ``commands``: HTTP command id dedup.

Example::

    writer = JournalWriter(session_factory, blobs=blobs)
    await writer.ensure_session(session_id)
    written = await writer.write(session_id, event, turn_id=run_id, trace_id=None, event_key="k")

    reader = JournalReader(session_factory, blobs=blobs)
    events = await reader.read(session_id, since=-1)
    latest = await reader.last_of(session_id, ["RunEnd"])
"""

from supervisor.journal.commands import CommandStore
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import (
    DeserializationError,
    SerializationError,
    decode_event,
    encode_event,
    event_type_name,
    jsonable,
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
    "jsonable",
]
