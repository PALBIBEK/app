# Cold resume SLO (Phase 1)

## Context

The supervisor keeps a **per-process** `SessionRegistry` for warm runtimes. Postgres holds the durable journal. After deploy or replica failover, a session may land on a peer without a warm runtime.

## SLO targets (staging)

| Path | Target | Notes |
|---|---|---|
| Warm turn (same replica) | p95 &lt; 2s | Runtime already in registry |
| Cold resume (journal + sandbox rebuild) | p95 &lt; 30s | Dominated by sandbox cold start |
| Cold resume (no sandbox, echo/kg) | p95 &lt; 3s | Registry rebuild only |
| Deploy drain | 0 mid-turn kills on stateless | Turn completes before stop |
| Deploy drain (sticky) | pause, status `paused` | Resume reuses paused sandbox when still on host |

## Routing options

1. **Session affinity** — ingress routes `session_id` to owning replica (Redis map or sticky cookie). Best warm latency.
2. **Cold resume only** — any replica; journal is source of truth. Simpler ops; higher tail latency.

Phase 1 staging (`deploy/staging/docker-compose.staging.yml`) uses **cold resume** between `supervisor-a` (8080) and `supervisor-b` (8081).

**Memory Service:** see `services/memory/docs/slo.md` for Tier 1 load/archive/lease/recall targets and dual-pod fence expectations (independent of supervisor warm registry).

## Idle reap

When `IDLE_REAP_MAX_SECONDS > 0`, sticky idle sessions are **paused** (sandbox kept); stateless idle sessions are **terminated**. Status becomes `paused` or `terminated` respectively. Resume from `paused` reuses an in-memory sandbox when still on the same replica.

## Measurement

Run `services/supervisor/scripts/measure_sandbox_metrics.py` on a Docker host and record results in `services/supervisor/ARCHITECTURE.md` Step 9 table.
