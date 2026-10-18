# Phase 2 Sprint 3 Completion Summary

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** Consolidate and harden the control-plane auth dependency layer

Sprint 3 did **not** rebuild JWT from the original roadmap sample. Identity stays
in FastAPI + PostgreSQL. Access tokens remain short-lived JWTs; refresh tokens
remain opaque, hashed, and rotated.

## Completed

### 3.1 JWT manager + PyJWT

- Added `prime_agent/web/dependencies/jwt_manager.py`
- Claim policy: `sub`, `iat`, `exp`, `jti`, `iss`, `aud`, `type=access`
- Algorithm pinned on decode; `PyJWTError` maps to a generic 401
- `AuthService` delegates encode/decode; opaque refresh stays in the service
- `python-jose` removed from `pyproject.toml`

### 3.2 Auth dependencies

- Shared `CurrentUser`, `OptionalUser`, `AdminUser`, `DbSession`
- Missing bearer credentials return 401 with `WWW-Authenticate: Bearer`
- `require_role` / `require_any_role` return generic 403
- `GET /api/v1/admin/ping` is a live admin gate

### 3.3 CSRF for cookie mutations

- Double-submit cookie `prime_agent_csrf` plus `X-CSRF-Token`
- Token is echoed in login/register/refresh JSON for the cross-origin SPA
- Required on `/auth/refresh` and `/auth/logout`
- Origin allowlist remains in place
- Frontend stores `csrfToken` in memory and sends it on refresh/logout

### 3.4 Tests

- New `tests/test_auth_dependencies.py` covers expiry, type, aud/iss,
  inactive users, missing auth, admin RBAC, and optional auth
- Cookie flow asserts CSRF rejection then success

## Verification

- Backend: 26 tests passing
- Frontend: 140 tests passing
- Targeted Ruff on changed files: passing
- No `python-jose` imports in `prime_agent/` or `tests/`

## Explicitly deferred

- Access-token `jti` denylist
- Permission / ABAC tables
- OAuth / JWKS / RS256
- TrustedHost production middleware
