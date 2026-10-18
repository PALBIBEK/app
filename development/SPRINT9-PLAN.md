# Phase 2 Sprint 9 Plan — D4 Sessions + SSE (Production Grade)

**Date:** 2026-08-30  
**Status:** Complete  
**Goal:** Production product loop — BFF owns sessions with D3 pins + D2 gates; real Test/Sessions/Traces/Stop over supervisor journal + resumable SSE. **No mock theater. No MVP echo shortcuts.**

Sources: `D4.md` §G acceptance (system BFF + UI) + AI harden §C/D.  
Patterns: `PYTHON_PATTERNS_MASTERCLASS.md` + Sprint 6–8 AiClient/bind/platform precedents.  
Research: four explore agents (recipe, system+FE audit, supervisor SSE, BFF+SSE patterns).

## Production slice (this sprint)

### AI plane harden
1. Drain fail-closed on **message** and **fork** (`503 draining`) — create already gated
2. Expose `total_cost` (+ token totals when available) on session detail/summary from `session_index`

### System BFF
1. `session_links` ownership (user ↔ ai_session_id ↔ harness_id + immutable pin snapshot)
2. `AiClient`: create, list, get, message, abort, terminate, fork, **stream_events** (byte proxy, long-lived timeout)
3. `SessionService` — all mutations owner-scoped; create = `ensure_runnable_pin` **only** (no client digests/runtime)
4. Full routes under `/api/v1/sessions*`:
   - CRUD list/get/create/delete(terminate)
   - messages (`command_id` BFF-minted or client-supplied; map `turn_id`)
   - events SSE + `Last-Event-ID`
   - abort, fork
   - pause/resume → **501** (never fake 200)
5. D2 `ensure_ready` on create **and** message
6. Cost rollup on list/detail from supervisor detail (not per-row SSE)

### Frontend (production surfaces)
1. Remap `sessionsAPI` + types to BFF contract (status map, abort≠stop naming honesty)
2. `fetch` + SSE reader helper (Bearer + `Last-Event-ID` reconnect)
3. WireEvent reducer: `run_*`, `turn_*`, `message_end`, `tool_start`/`tool_end`, `error`, `settled`
4. **Test** page: real create → SSE → message → Stop=abort; no `setTimeout` success
5. **Sessions** list + detail + harness sessions: un-mocked
6. **Traces**: session event view from SSE/journal (or dedicated events fetch) — no hard-coded mocks

## Explicitly out (named elsewhere — not “MVP defer”)

| Item | Track |
|------|--------|
| Multi-replica LiveHub / sticky SSE | **D12** |
| HTTP pause/resume + sandbox snapshot | Sandbox dimension |
| Live `message_chunk` token streaming polish | D5 / nice (journal `message_end` is authoritative) |
| User JWT on supervisor | Anti-goal |
| Derived catalog publish | D3 residual |
| Gateway/memory in `run_allowed` | D2 residual |

## Architecture freezes

1. Browser never calls supervisor  
2. Create body = `{ harness_id }` only (`extra=forbid`)  
3. Pins exclusively from `ensure_runnable_pin` (stored digests + live offered re-check)  
4. Service Bearer only upstream; user JWT never forwarded  
5. IDOR → **404** on all session ops  
6. Stop → abort; Delete → terminate; pause/resume → 501  
7. SSE credentials stay on BFF; FE uses authenticated `fetch` stream  
8. Journal is truth; SSE resume = `Last-Event-ID` = seq  

## Routes (complete)

| Verb | Path | Notes |
|------|------|--------|
| POST | `/api/v1/sessions` | `{ harness_id }` |
| GET | `/api/v1/sessions` | Owner list; optional `harness_id` |
| GET | `/api/v1/sessions/{id}` | Detail + cost |
| POST | `/api/v1/sessions/{id}/messages` | `{ content, command_id? }` → `{ turn_id, command_id, status }` |
| GET | `/api/v1/sessions/{id}/events` | SSE proxy |
| POST | `/api/v1/sessions/{id}/abort` | Stop |
| POST | `/api/v1/sessions/{id}/fork` | Optional `at_seq` |
| DELETE | `/api/v1/sessions/{id}` | Terminate |
| POST | `…/pause`, `…/resume` | **501** |

## Acceptance tests (must pass)

### System
- `test_create_session_unauthenticated_returns_401_before_upstream`
- `test_create_session_uses_ensure_runnable_pin_and_service_bearer_only`
- `test_create_session_unbound_returns_422_not_bound`
- `test_create_session_stale_bind_returns_422_stale_bind`
- `test_create_session_when_ai_not_ready_returns_503_ai_platform_not_ready`
- `test_create_session_inserts_ownership_row_with_pin_snapshot`
- `test_create_session_digest_fields_rejected`
- `test_message_mints_command_id_and_maps_turn_id`
- `test_message_when_ai_not_ready_returns_503`
- `test_get_message_events_abort_delete_fork_other_users_session_returns_404`
- `test_sse_proxy_forwards_last_event_id_and_content_type`
- `test_abort_maps_to_supervisor_abort`
- `test_delete_maps_to_supervisor_terminate`
- `test_pause_resume_return_501`
- `test_user_jwt_never_forwarded_to_supervisor_on_session_routes`
- `test_list_includes_cost_from_detail_projection`

### Supervisor
- `test_draining_blocks_message_returns_503`
- `test_draining_blocks_fork_returns_503`
- `test_session_detail_exposes_total_cost`

### Frontend
- Test: real session path; Stop → abort  
- Sessions/Traces: no mock data when authenticated  
- No `NEXT_PUBLIC_*` AI URLs/tokens  

## Implementation order

1. AI: drain-gate message/fork + cost on detail  
2. Migration + SessionLink  
3. Schemas + AiClient session/SSE (+ `ai_sse_timeout` / read=None)  
4. SessionService + routes  
5. Backend test matrix  
6. FE types/endpoints/SSE helper/reducer  
7. Wire Test + Sessions + Traces  
8. VibeSec + `SPRINT9-COMPLETION-SUMMARY.md`  
