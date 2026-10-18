# Phase 2 Sprint 8 Plan — D3 Catalog & Harness Bind (pin-only MVP)

**Date:** 2026-08-30  
**Status:** Complete  
**Goal:** Map product `harness.id` → immutable AI pin (`harness_version` + `composition_digest` + `runtime`). UI Test gated on **bound ∧ run_allowed**. Unlock D4 via `ensure_runnable_pin`.

Sources: `D3.md`, `contracts/composition.md`, Sprint 6/7 completion summaries.  
Patterns: `PYTHON_PATTERNS_MASTERCLASS.md` (§7 DTO, §6 Factory, §11 Service, §10 Errors, §12 Testing).  
Research: four parallel explore agents (D3 recipe, system+FE audit, AI catalog audit, bind-service pattern recipe).

## Slice (this sprint) — pin-only MVP

Bind product harnesses to **shipped** `coding-v1` composition after live catalog validation. **No** derived PromptPack/ToolSet/Composition publish from UI fields.

1. Alembic + model: pin columns (`harness_version`, `composition_id`, `composition_digest`, `runtime`, `bind_status`, `bound_at`, `bound_by`)
2. Schemas: `HarnessBindRequest` / `HarnessBindView`; extend harness GET/list responses
3. `AiClient.get_composition` (+ optional `list_compositions`) — service Bearer only
4. `HarnessBindService.bind_to_composition` + `ensure_runnable_pin` (D4 handoff)
5. `POST /api/v1/harnesses/{id}/bind` (owner-scoped; 404 IDOR)
6. Supervisor harden: reject session create when request `harness_version` ≠ composition’s `harness_version`
7. FE: bind badge + digests; Test disabled unless `bind_status==bound` **and** `run_allowed`; fail-closed while platform loading
8. Ops note: `YAL_PERSIST_CATALOG=true` for staging/prod

### Default MVP pin (validate live; do not blind-trust)

| Field | Value |
|-------|--------|
| `composition_id` | `coding-v1` |
| `composition_digest` | from catalog GET (fixture ref `sha256:bba8e14e…`) |
| `harness_version` | from composition body / shipped map `coding-v1` |
| `runtime` | `harness` (matches `coding-v1.yaml`; not browser-chosen) |

**Honest product note:** pin-only Run uses shipped composition tools/prompt — product `tools[]` / `system_prompt` stay CRUD until derived publish (follow-up).

## Explicitly out

| Item | Later |
|------|--------|
| Derived publish from UI prompt/tools | D3 follow-up |
| Real session create + SSE Test chat | **D4** |
| Gateway/memory in `run_allowed` | D2 residual |
| Catalog marketplace / multi-tenant artifacts | Later |
| Mode storage rewrite to sticky/stateless | Map at Run projection only if needed |
| Edit → auto-stale / republish | Follow-up (MVP pin ignores product field drift) |

## Architecture freezes

1. Product harness ≠ AI `harness_version` — always map explicitly  
2. Digests only — reject `latest` / floating refs  
3. Browser never calls `/v1/catalog` or `/v1/sessions`  
4. BFF sends **stored** pins only on Run (D4); browser never supplies digests  
5. Bind validates against **live** catalog; catalog outage → 503 `CATALOG_UNAVAILABLE` (no hopeful pins)  
6. Draft/unbound ≠ runnable  

## Runnable definition

```text
runnable =
  bind_status == bound
  AND composition still offered (last / ensure_runnable_pin validate)
  AND D2.run_allowed
```

## Error codes

| Code | HTTP | When |
|------|------|------|
| `NOT_BOUND` | 422 | `ensure_runnable_pin` unbound |
| `STALE_BIND` | 422 | stale / no longer offered |
| `COMPOSITION_NOT_OFFERED` | 422 | unknown / deprecated / floating at bind |
| `CATALOG_UNAVAILABLE` | 503 | supervisor catalog unreachable |
| `AI_PLATFORM_NOT_READY` | 503 | D2 gate inside `ensure_runnable_pin` |
| Ownership miss | 404 | never leak via bind codes |

## Implementation order

1. Freeze vocab + pin DTO  
2. Migration + model  
3. AiClient catalog GET  
4. Bind service + route  
5. `ensure_runnable_pin` + tests  
6. Supervisor version-mismatch refuse  
7. FE bind chrome + Test gate  
8. Persist-catalog ops note  
9. VibeSec + `SPRINT8-COMPLETION-SUMMARY.md`

## Test names (must pass)

### System BFF
- `test_bind_unauthenticated_returns_401_before_catalog_call`
- `test_bind_valid_shipped_composition_sets_bound_and_digests`
- `test_bind_unknown_digest_returns_422_composition_not_offered`
- `test_bind_floating_latest_rejected`
- `test_bind_deprecated_composition_returns_422_composition_not_offered`
- `test_bind_other_users_harness_returns_404`
- `test_bind_catalog_unreachable_returns_503_catalog_unavailable`
- `test_bind_uses_service_bearer_not_user_access_token`
- `test_ensure_runnable_pin_unbound_raises_not_bound`
- `test_ensure_runnable_pin_stale_raises_stale_bind`
- `test_ensure_runnable_pin_when_ai_not_ready_raises_ai_platform_not_ready`
- `test_harness_get_includes_unbound_bind_view_by_default`
- `test_product_crud_still_works_for_unbound_draft`

### Supervisor
- `test_create_session_harness_version_mismatch_vs_composition_returns_4xx`

### Frontend
- Unbound / draft → Test disabled  
- Bound + `run_allowed=false` → Test disabled  
- Bound + `run_allowed` → Test enabled (mock OK until D4)  
- Platform loading → fail closed (not optimistic enable)

## Minimal files to touch

**Backend:** new migration; `db/models/harness.py`; `schemas/harness.py` (+ bind); `services/ai_client.py`; new `harness_bind_service.py` + errors; `web/routes/harnesses.py`; `tests/test_harness_bind.py`

**Supervisor:** `session/manager.py` version match + test

**Frontend:** `harness.types.ts`; endpoints/hooks; `HarnessCard`; detail + test pages

## Research agents (this sprint)

| Focus | Outcome |
|-------|---------|
| D3 recipe / acceptance | Pin-only MVP; named matrix; D4 helper required |
| System+FE audit | No pin columns yet; Test gated by `run_allowed` only; create/detail already API-wired |
| AI catalog audit | GET summary lacks `harness_version` on wire — use body/map; mismatch check missing today |
| Bind pattern recipe | Mirror D2 service+MockTransport; separate `HarnessBindService` |

**Do not start coding until this plan is accepted / this chat proceeds to implement.**

*(Implemented 2026-08-30 — see `SPRINT8-COMPLETION-SUMMARY.md`.)*
