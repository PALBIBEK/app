# Phase 2 Sprint 8 Completion Summary

**Date:** 2026-08-30  
**Status:** Complete  
**Goal:** D3 pin-only MVP — map product `harness.id` → immutable AI composition pin; gate Test on bound ∧ `run_allowed`

Source: `prime-agent-study-main/development/ai-backend-production-grade/D3.md`  
Plan: `development/SPRINT8-PLAN.md`

## Shipped

### System BFF
- Alembic migration + pin columns (`bind_status`, `harness_version`, `runtime`, `composition_id`, `composition_digest`, `bound_at`, `bound_by`)
- `HarnessBindView` / `HarnessBindRequest` / `RunnablePin`; harness responses include nested `bind`
- `AiClient.get_composition` (+ `list_compositions`)
- `HarnessBindService.bind_to_composition` + `ensure_runnable_pin` (D4 handoff)
- `POST /api/v1/harnesses/{id}/bind` (owner-scoped; live catalog validate; reject floating `latest`)

### Supervisor
- `HarnessVersionMismatch` on session create when request `harness_version` ≠ composition pin
- Staging compose: `YAL_PERSIST_CATALOG=true`

### Frontend
- Bind types + `harnessesAPI.bind` + `useBindHarness`
- Card/detail: Bound/Draft/Stale badge; **Bind coding-v1** CTA; Test gated on bound ∧ platform ready (fail-closed while loading)
- Test page uses harness name/version (no hard-coded coding-v1 label for identity)

## Verification

| Suite | Result |
|-------|--------|
| Backend full | **116 passed** |
| Bind matrix (`test_harness_bind.py`) | **13 passed** |
| Supervisor hp6 + mismatch | **2 passed** |
| FE harness/platform Vitest | **28 passed** |

## VibeSec

**PASS** — BFF-only catalog; service Bearer; IDOR 404; floating rejected; no secrets in bind JSON; live digest validate before persist; supervisor version mismatch enforced.

## Residual / next

- Derived publish from UI prompt/tools (D3 follow-up)
- Real session create + SSE Test (**D4** consumes `ensure_runnable_pin`)
- Gateway/memory in `run_allowed` (D2 residual)
- Product tools/prompt remain decorative under pin-only MVP (documented honesty)
