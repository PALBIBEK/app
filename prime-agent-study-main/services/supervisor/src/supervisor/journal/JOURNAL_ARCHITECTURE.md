# Journal Architecture

The `journal/` folder implements an **append-only event log** for session durability and crash recovery.

---

## File Structure

```
journal/
├── __init__.py     → Public exports
├── writer.py       → JournalWriter (append events, allocate seq)
├── reader.py       → JournalReader (query events, detect unpaired)
├── serde.py        → Event serialization/deserialization
├── commands.py     → CommandStore (HTTP command deduplication)
└── JOURNAL_ARCHITECTURE.md
```

---

## Core Concepts

### Append-Only Log

Events are **never modified or deleted**. Each event gets a monotonically increasing sequence number (`seq`) allocated by the writer.

```
Session Timeline:
┌─────┬─────┬─────┬─────┬─────┬─────┬─────┐
│ seq │  0  │  1  │  2  │  3  │  4  │  5  │
├─────┼─────┼─────┼─────┼─────┼─────┼─────┤
│event│ Run │Turn │Tool │Tool │Msg  │Turn │
│     │Start│Start│Start│End  │End  │End  │
└─────┴─────┴─────┴─────┴─────┴─────┴─────┘
```

### Sequence Allocation

The writer atomically increments `sessions.last_seq` and assigns the new value. This guarantees:
- No gaps in sequence
- No duplicates
- Total ordering within a session

```sql
UPDATE sessions
SET last_seq = last_seq + 1
WHERE id = :session_id
RETURNING last_seq
```

---

## Components

### JournalWriter

Appends events to the journal with atomic sequence allocation.

```python
writer = JournalWriter(session_factory, blob_dir="/data/blobs")

# Ensure session exists
await writer.ensure_session(session_id, runtime="githi")

# Append single event
seq = await writer.append(session_id, event, turn_id=tid, trace_id=trace)

# Append batch atomically (N events, N seqs)
last_seq = await writer.append_batch(session_id, [
    (event1, turn_id, trace_id),
    (event2, turn_id, trace_id),
])
```

**Features:**
- Atomic batch writes
- Automatic `SessionIndexRow` updates (message count, cost, last event time)
- Large payload offload (>64KB → blob file)

### JournalReader

Queries events and detects incomplete operations for crash recovery.

```python
reader = JournalReader(session_factory, blob_dir="/data/blobs")

# Read all events since seq
envelopes = await reader.read(session_id, since=0)

# Find ToolStart without matching ToolEnd (crash recovery)
unpaired = await reader.unpaired_tool_starts(session_id)

# Get messages from most recent RunEnd
history = await reader.latest_history(session_id)

# Get current sequence number
last_seq = await reader.last_seq(session_id)
```

**Unpaired Detection:**
Used for crash recovery to find operations that started but never completed:
- `unpaired_tool_starts()` → Tools that need re-execution
- `unpaired_subagent_starts()` → Subagents that need cleanup
- `unpaired_compaction_starts()` → Compactions to resume

### CommandStore

HTTP command deduplication for exactly-once semantics.

```python
store = CommandStore(session_factory)

# First call: inserts and returns (True, response)
# Retry call: returns (False, original_response)
is_new, response = await store.claim(session_id, command_id, turn_id=tid)

if is_new:
    # Process the command
    ...
else:
    # Return cached response (idempotent)
    return response
```

---

## Event Serialization

Events are stored as JSON in the `payload` column with a `_type` discriminator.

```python
# Encode
data = encode_event(ToolStart(tool_call_id="tc-1", name="read", arguments={}))
# {"_type": "ToolStart", "tool_call_id": "tc-1", "name": "read", "arguments": {}}

# Decode
event = decode_event(data)
# ToolStart(tool_call_id="tc-1", name="read", arguments={})
```

**Registered Event Types:**
- `RunStart`, `RunEnd`
- `TurnStart`, `TurnEnd`
- `MessageEnd`
- `ToolStart`, `ToolEnd`
- `SubagentStart`, `SubagentEnd`
- `JobStart`, `JobEnd`
- `CompactionStart`, `CompactionEnd`
- `ApprovalRequired`, `ApprovalResolved`
- `MemoryRecall`
- `Settled`, `ErrorEvent`

---

## Blob Offload

Large payloads (>64KB) are stored as separate files to keep the events table lean.

```
Event payload > 64KB?
       │
       ├── YES → Write to blob_dir/{sha256}, store {"_type": "...", "_blob_ref": "sha256"}
       │
       └── NO  → Store full payload in events.payload
```

**Configuration:**
```python
writer = JournalWriter(
    session_factory,
    blob_dir="/data/journal-blobs",  # Where to store large payloads
    blob_threshold=64 * 1024,        # 64KB threshold
)
```

---

## Database Schema

```sql
-- Events table (append-only)
CREATE TABLE events (
    session_id VARCHAR(36) REFERENCES sessions(id),
    seq BIGINT,
    event_id VARCHAR(36) NOT NULL,
    turn_id VARCHAR(36),
    type TEXT NOT NULL,
    payload JSON NOT NULL,
    ts_ms BIGINT NOT NULL,
    trace_id TEXT,
    PRIMARY KEY (session_id, seq),
    UNIQUE (session_id, seq)
);

-- Commands table (deduplication)
CREATE TABLE commands (
    session_id VARCHAR(36) REFERENCES sessions(id),
    command_id TEXT,
    turn_id VARCHAR(36) NOT NULL,
    response JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (session_id, command_id)
);
```

---

## Crash Recovery Flow

```
Process Restart
      │
      ▼
┌─────────────────────────────────┐
│  Read last_seq from sessions    │
└─────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────┐
│  Find unpaired operations:      │
│  - ToolStart without ToolEnd    │
│  - SubagentStart without End    │
│  - CompactionStart without End  │
└─────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────┐
│  Resume or compensate each      │
│  unpaired operation             │
└─────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────┐
│  Continue from last_seq + 1     │
└─────────────────────────────────┘
```

---

## Key Design Decisions

1. **Append-only**: Events are immutable. History is never rewritten.

2. **Supervisor allocates seq**: The runtime never assigns sequence numbers. This prevents gaps and duplicates.

3. **Atomic batch writes**: Multiple events are written in a single transaction with contiguous sequence numbers.

4. **Blob offload**: Large payloads don't bloat the events table. Threshold is configurable.

5. **Typed errors**: `SerializationError` and `DeserializationError` provide actionable diagnostics.

6. **Command deduplication**: HTTP retries return the same response, enabling exactly-once semantics from the client's perspective.

---

## Usage Patterns

### Write + Read Loop

```python
# Writer appends during execution
seq = await writer.append(session_id, ToolStart(...), turn_id=tid, trace_id=trace)
# ... tool executes ...
seq = await writer.append(session_id, ToolEnd(...), turn_id=tid, trace_id=trace)

# Reader replays for UI or recovery
envelopes = await reader.read(session_id, since=last_seen_seq)
for env in envelopes:
    process(env.payload)
```

### Batch Append

```python
# Collect events during a turn
events = [
    (ToolStart(...), turn_id, trace_id),
    (ToolEnd(...), turn_id, trace_id),
    (MessageEnd(...), turn_id, trace_id),
]

# Write atomically
last_seq = await writer.append_batch(session_id, events)
```

### Cold Resume

```python
# After crash, find what needs completion
unpaired_tools = await reader.unpaired_tool_starts(session_id)
for tool_start in unpaired_tools:
    # Either re-execute or write ToolEnd with error
    ...
```
