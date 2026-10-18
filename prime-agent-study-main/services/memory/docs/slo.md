# Memory Service SLOs (Phase 5)

Complements supervisor `docs/cold-resume-slo.md` with Memory Service targets.

## Targets (staging)

| Path | Target | Notes |
|---|---|---|
| Sync `append` / adapter `note` | p99 &lt; 5 ms | In-process buffer only |
| Turn-end / checkpoint flush | p99 &lt; 50 ms | Durable write + optional outbox insert |
| `load` (hot, unrepaired) | p95 &lt; 100 ms | Postgres hot |
| `load` after archive hydrate | p95 &lt; 500 ms | Object store + parse JSONL |
| Lease acquire / renew | p95 &lt; 20 ms | |
| `recall` (bank ≤10k active/scope) | p95 &lt; 200 ms | HashingEmbedder; production embedder separate |
| Dual-pod stale fence reject | 100% | Loser appends rejected |

## Deploy / resume

- Memory Service sessions survive supervisor deploy independently.
- Adapter resume = Memory `load` (repaired transcript), not supervisor journal alone.
- Renew lease each turn; fence on every flush.

## Retention

- Hot window: `MEMORY_HOT_DAYS` (default 30).
- Archive: `MEMORY_ARCHIVE_DIR` filesystem (S3-compatible later).
- Archived sessions remain `load`-able; never silent drop.

## Multi-tenant

- App: TenantGate / required Scope on all Tier 2 ops.
- Postgres: apply `rls_postgres.sql` and set `app.tenant` per request.
