# Durable Memory Service (Tier 1) — normative

**QUEUE:** `durable-memory-tier1`  
**Research:** `agent-platform-research/Memory/memory.md` Phase 1  
**Placement:** `services/memory/` (sibling deployable; **MUST NOT** import `supervisor`)

## Principle

Agent-independent durable **session log** (Tier 1). Compaction/recall seam (`contracts/memory.md` / `ContextMemoryProvider`) is a different capability.

**Dual log (ADR-001):** supervisor journal = control-plane recovery facts; this service = harness-neutral `SessionEvent` transcript.

**Tool outbox ownership (ADR-004):** supervisor `invoke_tool` → supervisor `tool_outbox`; Memory adapter `tool/call` with `side_effect` → this service’s `outbox`. Do not cross-wire.

## Scope

```ts
interface Scope {
  tenant: string;
  subject: string;
  namespace: string;
}
```

All three **required** on `createSession`. Tenant omission is a client error (not a default).

## Protocol

### Tier 1

| Method | Role |
|---|---|
| `createSession(scope)` | Header only; no event 0 meta |
| `append` via write-behind buffer | Sync in-memory; durable on `flush` |
| `load(sessionId)` | Repairs open turns; returns logical session |
| `fork(sessionId, atSeq?)` | New session; lineage on header |
| `listSessions(scope)` | Filter by scope tuple |
| `acquireLease` / fence on flush | Multi-writer safety |
| `enqueueOutbox` + `executeOutbox` | Exactly-once side effects |

### Tier 2 (Phase 3)

| Method | Role |
|---|---|
| `write(scope, item)` | Insert memory item + embedding; provenance optional |
| `supersede(id, replacement)` | Link old→new; never in-place update |
| `get(id)` | Fetch including superseded (if not forgotten) |
| `forget(scope, selector)` | Soft-delete by id / source session / all-in-scope |
| `recall(scope, query, tokenBudget)` | Hybrid RRF pack; optional Tier 1 `memory/recall` log |
| `process_extraction_jobs` | Drain async extraction queue |

Tier 2 is a **rebuildable projection** of Tier 1 (`rebuild_tier2_from_tier1`).

## SessionEvent

```ts
interface SessionEvent {
  type: string;  // user/message | assistant/message | tool/call | tool/result
                 // step/start | step/end | turn/end | context/inject | memory/recall
  seq: number;   // contiguous from 0; equals index
  time: number;  // epoch ms
  data: unknown;
}
```

Same type API → storage. No conversion DTO.

## Durability rules

1. Sync buffer append; flush at semantic checkpoints only (no timers).
2. Assistant message flushed **before** tool dispatch.
3. `load` closes unanswered tool/calls with `{status:'interrupted', recovered:true}`; never truncates committed prefix.
4. Mid-log seq gap → `corrupt`; unknown `format_version` → reject.
5. tool/call with side effects: same txn as outbox row; execute from outbox with idempotency key.

## HTTP (v1)

| Method | Path |
|---|---|
| POST | `/v1/sessions` |
| POST | `/v1/sessions/{id}/events:buffer` |
| POST | `/v1/sessions/{id}/flush` |
| GET | `/v1/sessions/{id}` |
| POST | `/v1/sessions/{id}/fork` |
| GET | `/v1/sessions` |
| POST | `/v1/sessions/{id}/lease` |
| POST | `/v1/sessions/{id}/outbox:execute` |
| GET | `/v1/healthz` |
| POST | `/v1/memory/items` |
| GET | `/v1/memory/items/{id}` |
| POST | `/v1/memory/items/{id}/supersede` |
| POST | `/v1/memory/recall` |
| POST | `/v1/memory/forget` |
| POST | `/v1/memory/erase_subject` |
| POST | `/v1/sessions/{id}/archive` |
| GET | `/v1/metrics` |
| POST | `/v1/memory/extraction:process` |

## Phase 5 — Operations

| Concern | Mechanism |
|---|---|
| Retention | `archive_session` → object store JSONL; `load` rehydrates; never silent drop |
| Audited forget | `actor` + `reason` → `forget_audit` |
| Subject erase | Scoped by `tenant`+`subject` only |
| Tenant gate | `require_scope` / `TENANT_REQUIRED`; Postgres: `rls_postgres.sql` |
| Metrics | `GET /v1/metrics` — recall_precision, extraction_yield, bank_growth_vs_supersession, resume_success_rate |
| Cost | `CachingEmbedder`; barren `turn/end` skips extraction enqueue |
| SLO | `services/memory/docs/slo.md` |

## Phase 3 — Semantic bank

- **Schema:** `memory_item` (embedding JSON for SQLite; pgvector optional later), `extraction_job`
- **Extraction:** enqueue on Tier 1 flush when `turn/end` present; stingy heuristic (prefs/decisions/corrections/facts); worker errors never fail the turn
- **Recall:** vector + lexical + recency + confidence → RRF → pack to `max_tokens`; log `memory/recall` with `item_ids` when `log_session_id` set
- **Supersede only** — no content update API

## Phase 2 — Pi / YAL harness adapter

**Placement:** `harness/src/extensions/memory/durable/`  
**Bind:** `.yal/memory_service.json` (materialized when `MEMORY_SERVICE_URL` is set)

```json
{
  "base_url": "http://memory:8090",
  "session_id": "<supervisor-session-id>",
  "scope": { "tenant": "...", "subject": "...", "namespace": "facts" },
  "lease_owner": "optional-pod-id"
}
```

| Method | Role |
|---|---|
| `start()` | `createSession` / attach + optional lease (awaited) |
| `pushEvent(type, data)` | **Sync** local buffer only — no HTTP |
| `flush()` | `events:buffer` + `flush` (awaited checkpoint) |
| `resume()` | `load` → repaired transcript |

`registerDurableMemory(api)` wires `session_start` / `session_flush` when the bind file exists.  
**Not** the same as ContextMemory `.yal/memory_rpc.json` (compaction/recall).

## Phase 4 — Second adapter (agent-independence)

**Placement:** `services/memory/src/memory_service/adapters/simple_loop.py`  
**Adapter #2:** `SimpleLoopAdapter` — minimal Python loop (no Pi, no `.yal`).

| Method | Role |
|---|---|
| `begin()` | create/attach session (awaited) |
| `note(type, data)` | **Sync** local buffer |
| `checkpoint()` | HTTP buffer + flush |
| `resume()` | HTTP load (repaired) |
| `recall()` / `write_fact()` | Tier 2 via same HTTP protocol |

Deliberate differences from Pi: explicit checkpoints, caller-supplied scope, no harness hooks. **Protocol unchanged.**

## Fail-loud codes

| Code | When |
|---|---|
| `SCOPE_REQUIRED` | Missing tenant/subject/namespace |
| `SESSION_CORRUPT` | Seq gap / parse error mid-log |
| `FORMAT_UNSUPPORTED` | Unknown format_version |
| `STALE_FENCE` | Append/flush with stale lease fence |
| `LEASE_HELD` | Cannot acquire lease |
| `SESSION_NOT_FOUND` | Unknown id |
