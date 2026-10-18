# Agent Introspection Contract

What any agent runtime must expose so an external supervisor can observe,
persist, recover, and control it — without knowing how the agent works inside.

Two surfaces: **events** (pushed) and **commands** (pulled).

---

## Principle

The supervisor never inspects agent internals. It only reads what the agent
declares. So the agent must declare enough to answer four questions:

1. What is happening right now?      → events
2. What has happened so far?         → history command
3. Is it safe to act?                → state command
4. What did it cost?                 → stats command

---

## Events (agent → supervisor, streamed)

### Required

| Event | Carries | Why the supervisor needs it |
|---|---|---|
| `run_start` | run id | marks a unit of work beginning |
| `run_end` | full message history | authoritative record; recovery baseline |
| `turn_start` | — | one model round-trip begins |
| `turn_end` | message, tool results | round-trip boundary |
| `message_end` | complete message, usage, cost | the only message event worth persisting |
| `tool_start` | **tool call id**, name, args | written *before* execution — proves intent |
| `tool_end` | **same id**, result, isError | proves completion |
| `settled` | — | nothing pending: no retry, compaction, or queued work |
| `error` | class, retryable, detail | classify without parsing strings |

### Rules that make these usable

- **`tool_start` must be emitted before the tool runs.** A `tool_start`
  without a matching `tool_end` is the only signal that says "outcome
  unknown." Without it, a crashed call looks identical to one that never
  happened.
- **Pair on a stable id.** Both events carry the same tool call id.
- **`settled` ≠ `run_end`.** A run can end and still be followed by a retry
  or compaction. Turn completion keys on `settled`.
- **Streaming deltas are live-only.** Never persist them. `message_end` is
  authoritative.

### Recommended

`retry_start` / `retry_end` (attempt, max, delay) — so the supervisor can
detect nested retries and error-budget burn.

### Compaction / recall (session memory)

`compaction_start` / `compaction_end` (tokens before/after) — so context loss
is visible, not silent. Pair on stable `compaction_id` (same pattern as tools).

| Event | Carries | Why |
|---|---|---|
| `compaction_start` | `compaction_id`, `reason`, `tokens_before`, `first_kept_entry_id?` | Intent **before** summarization |
| `compaction_end` | **same** `compaction_id`, `stop_reason`, `tokens_after?`, `summary_digest?` | Pairs with start; crash = unpaired start |
| `memory_recall` | `block_id`, `source`, `token_estimate`, `injected` | Observability for semantic injection |

See [`memory.md`](./memory.md) for the `ContextMemoryProvider` seam.

`approval_request` / `approval_response` (id, prompt, options, timeout) — a
blocking round-trip lets the supervisor gate risky actions without touching
the loop.

### Subagent / job (session tree)

Child agents and background jobs are first-class journal events so the
supervisor can rebuild a session tree after restart without inspecting harness
internals (see [`subagent.md`](./subagent.md)).

| Event | Carries | Why |
|---|---|---|
| `subagent_start` | `run_id`, `child_session_id`, `parent_session_id`, `mode`, `provider`, `label?` | Intent + tree edge **before** / at child start |
| `subagent_end` | **same** `run_id`, `stop_reason`, `output?` | Pairs with start; crash = unpaired start |
| `job_start` | `job_id`, `kind` (`bash` \| `pty` \| `subagent` \| …) | Background work handle |
| `job_end` | **same** `job_id`, `stop_reason`, `output?` | Job completion |

`mode`: `one_shot` \| `fork` \| `continuable` \| `background`.

Pair on stable `run_id` / `job_id` the same way tools pair on `tool_call_id`.

---

## Commands (supervisor → agent, request/response)

### Required

**`get_state`** — the readiness probe.
```
{ session_id, is_streaming, is_compacting, pending_count,
  model, context_window, message_count }
```
Settled = `!is_streaming && !is_compacting && pending_count == 0`.
Needed even if a `settled` event exists — after a supervisor restart, there
is no event to have missed.

**`get_history`** — full conversation, replayable.
Must be complete enough to reconstruct the session on a fresh process.

**`abort`** — cancel in-flight work. Must cancel the model call *and* the
running tool, and both cancellations must appear as events.

**`get_capabilities`** — which commands and event types this build supports.
Exposed on the supervisor-local `AgentRuntime` Protocol (see
[`agent_core.md`](./agent_core.md)); without it, the supervisor discovers gaps
at runtime instead of at startup.

### Recommended

**`get_stats`** — tokens, cost, context usage percent. Enables budget caps.
Derivable from `message_end` usage, but a direct read is cheaper and survives
restarts.

**`get_history(since: cursor)`** — incremental catch-up. Turns recovery from
"replay everything" into "fetch the delta." Requires stable, ordered entry
ids.

**`fork(at: entry_id)`** — branch a session. Nearly free if history is
append-only and immutable.

### Subagent / child commands (recommended)

Harness-neutral; supervisor maps these onto `SubagentTransport` + journal
events (see [`subagent.md`](./subagent.md)). Names are contract vocabulary —
not Cordis / DeepSeek host types.

| Command | Returns |
|---|---|
| `spawn_child` | `{ child_session_id, run_id? }` |
| `fork_child` | `{ child_session_id }` |
| `start_continuable` | `{ child_id, message_id? }` |
| `send_child_message` | `{ message_id? }` |
| `interrupt_child` | void |
| `list_children` | `ChildRow[]` (`direct` \| `descendants`) |
| `job_list` / `job_output` / `job_kill` | job handles / void |

---

## Version handshake

Every agent build declares:

```yaml
protocol_version: 1
agent_version: 0.4.2
```

The supervisor refuses to start a build whose protocol it doesn't speak.
Fail at spawn, not three events into a session.

A session is pinned to one version for its lifetime. History written by one
harness must be read by the same harness — tool schemas and prompt structure
are embedded in it.

---

## Minimum viable set

If you build only five things:

```
tool_start / tool_end   (paired id, start written first)
message_end             (with usage)
run_end                 (full history)
get_state               (settled check)
abort
```

That is enough for: durable logging, crash detection, cost tracking,
resume, and cancellation.

---

## Anti-patterns

**Logging only on success.** Makes unknown-outcome crashes invisible.

**Cumulative state in every delta.** Bloats the stream and tempts the
supervisor to persist redundant copies.

**Error strings instead of error classes.** Forces the supervisor to parse
prose to decide whether to retry.

**Internal retries with no events.** The supervisor adds its own layer and
load silently multiplies.

**No capability query.** Every version difference becomes a runtime failure.