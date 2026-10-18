# Phase 2 Sprint 4 Plan — Testing & Integration

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** Prove service-layer business rules and remaining authz/CSRF gaps. Do not clone existing route CRUD tests.

Sprint 2–3 already cover httpx CRUD, GET IDOR, CSRF-missing refresh, JWT dependency matrix, and schema mass-assignment on users. The original roadmap sample that re-runs harness CRUD over HTTP would be duplicate work.

Research sources:

- `PYTHON_PATTERNS_MASTERCLASS.md` (AAA, factories, service tests)
- `08-testing-patterns.md` (pytest-asyncio, fixtures)
- `ANTI_PATTERNS_AND_BEST_PRACTICES.md` (no private-helper tests, no duplicated setup)
- Current `backend/tests/` (26 cases, no `conftest.py`)

---

## Architectural rule

Test **behavior at the layer that owns it**.

| Layer | Owns | Do not re-test here |
|---|---|---|
| `tests/services/` | Validation, ownership, auth domain rules | Pagination JSON, OpenAPI paths |
| Existing `test_*_routes.py` | HTTP status, cookies, owner list isolation | Every `pre_save_check` branch |
| Sprint 5 | Live dashboard / `.env.local` | Not this sprint |

Identity stays in the control plane. Do not invent sandbox, OAuth, or ABAC tests.

---

## Task breakdown

### Task 4.1 — Shared fixtures + service tests  **HIGH**

- Add `tests/conftest.py` with a shared async `db_session` and user factory
- Add `tests/services/test_harness_service.py`
- Add `tests/services/test_tool_service.py`
- Add `tests/services/test_user_service.py`
- Add `tests/services/test_auth_service.py`
- Add `tests/services/test_base_service.py`
- Set `asyncio_default_fixture_loop_scope = "function"`
- Remove duplicated `db_session` engines from existing test modules

### Task 4.2 — Close HTTP security gaps, not a second CRUD flow  **HIGH**

- IDOR on PUT/DELETE for harnesses and tools → 404
- `HarnessCreate` / `ToolCreate` reject `user_id` (mass assignment)
- CSRF mismatch and missing CSRF on logout
- Login happy path: cookies + `csrf_token`, no `refresh_token` in JSON
- Do **not** add `tests/integration/test_harness_api.py` CRUD clone

### Out of scope

- Frontend E2E / Sprint 5 dashboard
- `jti` denylist, OAuth, sandbox
- `assert True` / empty `post_save` tests
- Padding to hit 50 by splitting one assertion five ways

**Honest bar:** ~55–70 meaningful tests; `prime_agent/services/` coverage should rise sharply. 50+ is a floor on real cases, not a vanity count.

---

## Implementation order

1. `conftest.py` + migrate existing tests green
2. Service tests
3. CSRF/login + IDOR mutate + schema forbid
4. Full pytest + targeted Ruff
5. `SPRINT4-COMPLETION-SUMMARY.md`
