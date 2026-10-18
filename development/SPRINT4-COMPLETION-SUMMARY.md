# Phase 2 Sprint 4 Completion Summary

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** Service-layer business rules and remaining control-plane authz/CSRF coverage

Sprint 4 did **not** clone the roadmap harness HTTP CRUD sample. Route CRUD was
already covered in Sprint 2. This sprint tests the layer that owns business
rules and closes IDOR/CSRF/login gaps.

## Research applied

- SQLAlchemy 2.0 external-transaction recipe: bind session to connection with
  `join_transaction_mode="create_savepoint"`, `expire_on_commit=False`, outer
  rollback (async-workflows / FastAPI Patterns / SQLAlchemy #10857).
- SQLite SAVEPOINT listeners (`isolation_level=None` + manual `BEGIN`) +
  `StaticPool` for `:memory:`.
- Corridor: no pytest suite to copy; only session/service patterns already in
  `prime_agent/`.
- VibeSec: IDOR → 404, CSRF double-submit, generic auth errors, SSRF literal
  blocks, mass-assignment forbid.

## Completed

### 4.1 Shared fixtures + service tests

- `tests/conftest.py` — savepoint-isolated `db_session`, `make_user`, `user`
- `tests/services/test_harness_service.py` — validation, ownership, lookups
- `tests/services/test_tool_service.py` — validation, SSRF unit, lookups
- `tests/services/test_user_service.py` — profile allowlist, email conflict, password revoke
- `tests/services/test_auth_service.py` — register/auth branches, tokens, inactive/expired
- `tests/services/test_base_service.py` — CRUD 404 / filters / count contracts
- `asyncio_default_fixture_loop_scope = "function"`
- Route/auth modules use shared `db_session` (no duplicated engines)

### 4.2 Security gaps (not a second CRUD flow)

- PUT/DELETE of another user’s harness or tool → 404 (in route suites)
- Harness/tool schemas reject injected `user_id` (`test_app.py`)
- CSRF mismatch, missing CSRF cookie, missing CSRF on logout
- Login: CSRF cookie + `csrf_token` JSON; HttpOnly refresh; no `refresh_token` in body

## Verification

| Metric | Result |
|--------|--------|
| Backend tests | **85 passed** |
| `prime_agent/services/` | auth 98%, base 86%, harness 99%, tool 96%, user 100% |
| Overall `prime_agent` | **90%** |
| Targeted Ruff on Sprint 4 tests | passing |
| VibeSec review | Pass — tests strengthen CSRF/IDOR/SSRF/auth controls; no production fixes required |

## Explicitly deferred (Sprint 5+)

- Live dashboard / `.env.local` / CORS/401 browser UX
- `jti` denylist, OAuth, TrustedHost
- DNS-resolve SSRF hardening beyond literal/private hostname blocks
