# yal-memory-service

Independent durable **Memory Service** for the YAL agent platform (Tier 1 session log + Tier 2 semantic bank).

- **Contract:** `../../contracts/durable_memory.md`
- **Research:** `agent-platform-research/Memory/memory.md`
- **Does not** import `supervisor`. Developable alone.

## Run

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
MEMORY_DATABASE_URL=sqlite+aiosqlite:///./memory.db python -m memory_service
```

Default HTTP port: `8092` (`MEMORY_HOST` / `MEMORY_PORT`).

## Semantics

### Tier 1
| Concern | Behavior |
|---|---|
| Scope | Required `{tenant, subject, namespace}` |
| Append | Sync in-memory buffer (no I/O) |
| Flush | Durable at semantic checkpoints only (no timers) |
| Load | Repairs open tool calls; never truncates; rejects corrupt gaps / unknown format |
| Outbox | `tool/call` with `side_effect: true` enqueued in same flush txn |
| Lease | Fenced owner; stale fence rejected on flush |

### Tier 2
| Concern | Behavior |
|---|---|
| write / supersede / get / forget | Supersede-only lineage; soft forget |
| recall | Hybrid RRF + token budget; optional Tier 1 `memory/recall` log |
| extraction | Async on `turn/end` flush; stingy heuristic; failures isolated |

### Ops (Phase 5)
| Concern | Behavior |
|---|---|
| Archive | `POST /v1/sessions/{id}/archive` → JSONL; `load` rehydrates |
| Erase | Audited `forget` + `erase_subject` (tenant+subject) |
| Metrics | `GET /v1/metrics` |
| Cost | Embed cache; skip barren `turn/end` extraction |
| Tenant | `TenantGate`; Postgres `rls_postgres.sql` |
| SLO | `docs/slo.md` |

## Limits

- SQLite for local/CI; Postgres via `postgresql+asyncpg://...`
- Embeddings: `HashingEmbedder` default; swap `Embedder` for production / pgvector
- Supervisor journal remains a separate control-plane log

## Extension

Adapters should: buffer on hot path → flush at checkpoints → `load` / `recall` via HTTP.

- **#1 Pi:** `harness/src/extensions/memory/durable/` + `.yal/memory_service.json`
- **#2 Simple loop:** `memory_service.adapters.SimpleLoopAdapter` (constructor scope, `begin`/`note`/`checkpoint`)
