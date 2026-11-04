# yal-memory-service

Independent durable **Memory Service** for the YAL agent platform (Tier 1 session log + Tier 2 semantic bank).

- Imports nothing from the rest of `supervisor` (`tests/memory_store` checks it).

## Run alone

```bash
cd services/supervisor
MEMORY_DATABASE_URL=sqlite+aiosqlite:///./memory.db \
  SERVE_SUPERVISOR_API=0 SERVE_MODEL_GATEWAY=0 python -m supervisor.main   # :8092
pytest tests/memory_store
```

Default HTTP port: `8092` (`MEMORY_HOST` / `MEMORY_PORT`).

## Semantics

### Tier 1
| Concern | Behavior |
|---|---|
| Scope | Required `{tenant, subject, namespace}` |
| Append | Sync in-memory buffer (no I/O) |
| Flush | Durable at semantic checkpoints; events between checkpoints live only in one replica's memory, so a crash or deploy loses them (at most `MAX_PENDING_EVENTS` = 10,000 per session; a full buffer refuses appends with `BUFFER_FULL` until flushed) |
| Idle buffers | Swept every minute: a buffer untouched for 10 minutes is flushed, or dropped if its session does not exist; the seq an append returns is provisional until its flush |
| Sessions | A duplicate `session_id` is `SESSION_EXISTS` (409); a fork point outside `-1..last_seq` is `INVALID_REQUEST` (422) |
| Load | Repairs open tool calls; never truncates; rejects corrupt gaps / unknown format |
| Outbox | `tool/call` with `side_effect: true` enqueued in same flush txn |
| Lease | Fenced owner; stale fence rejected on flush |

### Tier 2
| Concern | Behavior |
|---|---|
| write / supersede / get / forget | Supersede-only lineage; soft forget |
| recall | Hybrid RRF + token budget; optional Tier 1 `memory/recall` log |
| extraction | Queued on `turn/end` flush (durable cursor: each job starts after the last); a background worker claims and runs jobs every `MEMORY_EXTRACTION_INTERVAL_S` (default 5, 0 = off); a failed run is retried after 30 s, 1, 2, 4 min (5 runs), then the job is `error` until `POST /v1/memory/extraction:retry_failed`; backlog and failures in `/v1/metrics` |

### Notebooks (long-term memory)
| Concern | Behavior |
|---|---|
| Owner | One tenant, one owner (principal), one exact agent version (`agent_pin`) |
| Ids | `<tenant>~<hex>`: any caller can address the right tenant; the stored tenant is still checked |
| Facts | Stable ids; provenance (sensitivity, sources) only tightens |
| Changes | One locked transaction each: capture (deduplicated), edit, delete, clear, restore |
| History | Every change a revision; read the facts at any revision; restore never declassifies |
| Bank | Each live fact that is the owner's own, ordinary and fully traced is also a bank item |
| Erase | `erase_subject` for `user:<owner>` removes their notebooks, history included |
| Routes | `/v1/notebooks…`; the supervisor's `supervisor.memory.client` is the client |

Who may *see* a fact (disclosure across audiences) is decided by the supervisor,
which knows scopes and their members; this service never imports it.

### Ops (Phase 5)
| Concern | Behavior |
|---|---|
| Archive | `POST /v1/sessions/{id}/archive` → JSONL; `load` rehydrates |
| Erase | Audited `forget` + `erase_subject` (tenant+subject) |
| Metrics | `GET /v1/metrics` |
| Cost | Embed cache (LRU, 20,000 vectors per process); skip barren `turn/end` extraction |
| Database | `MEMORY_DATABASE_URL`; unset falls back to `./memory.db` locally only: with `ENV` production or staging it must be set, and in production it must not be SQLite (unless `MEMORY_ALLOW_SQLITE=1`) |
| Tenant | `TenantGate`; Postgres `rls_postgres.sql` |

## Limits

- SQLite for local/CI; Postgres via `postgresql+asyncpg://...`
- Embeddings: `HashingEmbedder` default; swap `Embedder` for production / pgvector
- Supervisor journal remains a separate control-plane log

## Extension

Adapters should: buffer on hot path → flush at checkpoints → `load` / `recall` via HTTP.

