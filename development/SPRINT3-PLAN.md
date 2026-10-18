# Phase 2 Sprint 3 Plan — Async Auth Dependencies

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** Consolidate, harden, and prove the control-plane auth dependency layer. Do not rebuild JWT or copy the original roadmap sample.

Sprint 2 already shipped working async `get_current_user`, opaque rotating refresh cookies, owner-scoped CRUD, and claim-pinned access JWTs. The original Sprint 3 sketch in `PHASE2-BACKEND-ROADMAP.md` (inline `python-jose` decode with only `sub`) would be a **security regression**. This plan replaces that sketch.

Research sources:

- `agent-platform-research-insight/` (control-plane identity, FastAPI dep layering, service-layer authz)
- `corridor-platforms-REQ-847` (PyJWT `JWTManager`, cookie refresh, `WithAuthorizationService`)
- Current `prime_agent/` Sprint 2 implementation
- 2026 FastAPI production practice: `Annotated` deps, PyJWT, cookie CSRF, auth in dependencies not middleware

---

## Architectural rule (non-negotiable)

Identity and authorization stay in the **control plane** (FastAPI + PostgreSQL).

```text
Request
  → HTTPBearer (access JWT in memory)
  → get_current_user
       → JWTManager.decode (sync crypto: iss/aud/jti/exp/type=access)
       → await load User from DB
       → reject inactive
  → optional require_role / require_any_role
  → thin route
  → service-layer ownership / object checks
  → never pass user JWTs or cloud credentials into a sandbox
```

- Workspace = durable user data
- Sandbox = untrusted execution environment
- Control plane = principal → scope → grants

Sprint 3 does **not** build sandbox capabilities, OAuth/SSO, or a full permission table. Those belong later.

---

## Starting point (already done)

| Capability | Location | Sprint 3 action |
|---|---|---|
| Async `get_current_user` | `web/dependencies/auth.py` | Consolidate, do not rewrite decode here |
| `require_role` / `require_any_role` / `get_optional_user` | same file | Test; export; use on one real gate |
| Access JWT `sub`/`iat`/`exp`/`jti`/`iss`/`aud`/`type` | `services/auth_service.py` | Extract to JWTManager |
| Opaque refresh + family revoke | `AuthService` | Keep; do not add JWT refresh |
| Cookie refresh + Origin allowlist | `web/routes/auth.py` | Add CSRF on top |
| Owner-scoped harness/tool CRUD | services + routes | Do not move IDOR into role deps |
| `HTTPBearer` (not `OAuth2PasswordBearer`) | `auth.py` | Keep |

**JWT is not async.** Encode/decode is CPU-bound. Real async work is the user DB fetch. Do not wrap JWT in `to_thread` unless profiling shows a need.

---

## Out of scope (defer)

| Item | Why later |
|---|---|
| Access-token `jti` denylist / Redis | 30-minute TTL + refresh family revoke is enough |
| Permission / ABAC matrix / `WithAuthorizationService` | Owner `user_id` filters already cover CRUD |
| Authlib OAuth / SAML / JWKS / RS256 | No external issuer yet |
| API-key hybrid auth | Corridor-specific; not Phase 2 |
| TrustedHost + reverse-proxy hardening | Sprint 4 production middleware |
| `__Host-` cookie prefix | After HTTPS-only prod is locked |
| Frontend `can()` RBAC UI | Backend must be the source of truth first |

---

## Task breakdown

### Task 3.1 — JWT manager extraction + PyJWT  **HIGH**

**Intent:** Isolate sync crypto and claim policy. Keep refresh persistence in `AuthService`.

**Files**

- Add `prime_agent/web/dependencies/jwt_manager.py` (or `prime_agent/security/jwt.py` re-exported from dependencies)
- Slim `prime_agent/services/auth_service.py` to call the manager for access tokens only
- Replace `python-jose[cryptography]` with `PyJWT` in `pyproject.toml`
- Update tests that `from jose import jwt`

**Do**

- `create_access_token(user_id)` sets `sub`, `iat`, `exp`, `jti`, `iss`, `aud`, `type=access`
- `decode_access_token` pins `algorithms=[...]` and requires `aud`/`iss`/`exp`/`iat`/`jti`/`sub`
- Reject `type != "access"` at the manager or at `get_current_user_from_token` — one place only
- No refresh JWT helpers

**Do not**

- Copy `PHASE2-BACKEND-ROADMAP.md` Task 3.2 sample
- Copy Corridor `identity: {user_id}` payload (Prime uses `sub`)
- Duplicate decode in both `auth.py` and `AuthService`

**Acceptance**

- [ ] python-jose is gone from runtime and tests
- [ ] Existing `test_auth_tokens.py` still passes
- [ ] Wrong `aud` / `iss` / expired / wrong `type` → 401
- [ ] JWT methods are sync; only DB paths are `async`

**Estimate:** 2–3 hours (library swap + claim tests, not the original 1 hour)

---

### Task 3.2 — Auth dependency consolidation  **HIGH**

**Intent:** One DI surface for routes. Fix B008 (`Depends()` in defaults). Make role gates real.

**Files**

- `prime_agent/web/dependencies/auth.py`
- `prime_agent/web/dependencies/__init__.py`
- Route modules currently redefining `CurrentUser` locally (`auth.py`, `users.py`, `harnesses.py`, `tools.py`)

**Do**

- Module-level aliases:

```python
DbSession = Annotated[AsyncSession, Depends(get_db)]
BearerCreds = Annotated[HTTPAuthorizationCredentials, Depends(security)]
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_role("admin"))]
```

- `get_current_user` stays thin: extract bearer → `AuthService.get_current_user_from_token`
- Confirm inactive users return **403**, missing/invalid tokens **401** (generic detail)
- Export `require_role`, `require_any_role`, `get_optional_user`, `CurrentUser`
- Attach `require_role("admin")` to one non-public control-plane route (or a dedicated `/admin/ping` that 403s members). Dead security APIs are a hazard.
- Keep generic error strings (no “token expired” vs “bad signature” split to clients)

**Do not**

- Trust JWT `role` claims; always reload `User` from DB
- Replace service-layer ownership with `require_role`
- Put auth in middleware

**Acceptance**

- [ ] Routes import `CurrentUser` from `web.dependencies`, not local copies
- [ ] Ruff B008 clean on `auth.py`
- [ ] Member hitting an admin gate → 403
- [ ] Missing `Authorization` → 401 with `WWW-Authenticate: Bearer`
- [ ] Optional auth returns `None` on missing or garbage token (no 401)

**Estimate:** 2–3 hours

---

### Task 3.3 — CSRF for cookie-authenticated mutations  **HIGH**

**Intent:** Origin allowlist is necessary but not sufficient for cookie refresh.

**Files**

- `prime_agent/web/routes/auth.py` (or `web/dependencies/csrf.py`)
- `prime_agent/config/settings.py`
- Frontend `src/lib/api/client.ts` refresh path
- `web/middlewares/cors.py` already allows `X-CSRF-Token`

**Recommended design (double-submit)**

1. On login/register/refresh, set a second cookie: `csrf_token` (**not** HttpOnly), `SameSite=Lax`, path scoped to `/api/v1/auth`
2. Browser reads it and sends `X-CSRF-Token` on `POST /auth/refresh` and `POST /auth/logout`
3. Server compares cookie and header with constant-time equality
4. Keep `require_trusted_origin` as defense in depth
5. Access JWT remains Bearer-only; CSRF does not apply to Bearer routes

**Acceptance**

- [ ] Refresh without CSRF header (browser Origin present) → 403
- [ ] Refresh with matching CSRF + trusted Origin → 200 and rotated cookie
- [ ] Non-browser clients with no `Origin` still work if CSRF cookie+header match, or a documented exception for same-site tests
- [ ] Refresh token still never appears in JSON
- [ ] Frontend single-flight refresh sends the CSRF header

**Estimate:** 3 hours (backend + frontend + tests)

---

### Task 3.4 — Dependency and JWT test matrix  **HIGH**

**File:** `backend/tests/test_auth_dependencies.py` (plus CSRF cases in `test_auth_tokens.py` or a sibling)

| # | Case | Expected |
|---|---|---|
| 1 | Valid access bearer | 200, current user |
| 2 | Expired access | 401 |
| 3 | Refresh token as Bearer | 401 (`type`) |
| 4 | Wrong `aud` / `iss` | 401 |
| 5 | Inactive user | 403 |
| 6 | Missing Authorization | 401 |
| 7 | `require_role("admin")` as member | 403 |
| 8 | Optional user + garbage token | `None` / public 200 |
| 9 | CSRF missing on refresh | 403 |
| 10 | Existing rotation/reuse/logout family revoke | still pass |

Also keep: owner-scoped harness/tool tests (regression that role deps did not replace IDOR checks).

**Estimate:** 2–3 hours

---

### Task 3.5 — Hygiene (same sprint if time)  **MEDIUM**

- Docstrings in `auth.py`: stop saying “Token validation with jose”
- `require_role` 403 detail: avoid echoing requested role if that leaks policy; prefer `"Insufficient permissions"`
- Confirm `get_optional_user` does not instantiate `HTTPBearer(auto_error=False)` in a default (B008)
- Do **not** expand full-repo Ruff cleanup of legacy `base.py` / mixins unless it blocks this sprint

**Estimate:** 30–45 minutes

---

## Layering (file layout)

```text
prime_agent/
  services/auth_service.py     # register, login, opaque refresh rotate/revoke, load user from token
  web/dependencies/
    jwt_manager.py             # sync encode/decode + claim policy (PyJWT)
    auth.py                    # FastAPI DI only
    database.py                # get_db / DbSession
    csrf.py                    # optional: CSRF compare helper
    __init__.py                # CurrentUser, AdminUser, get_current_user, require_role
  web/routes/auth.py           # cookies, Origin, CSRF, thin handlers
  config/settings.py           # jwt_*, cookie_*, csrf cookie name
```

**Layering rule**

| Module | Owns |
|---|---|
| `jwt_manager` | Sync crypto and claim policy |
| `auth_service` | Async persistence and refresh family |
| `dependencies/auth.py` | FastAPI DI |
| `routes/auth.py` | HTTP cookies, Origin, CSRF |

---

## Security checklist (VibeSec / OWASP)

- [ ] Pinned algorithm; never `algorithms=["HS256", "none"]`
- [ ] `aud` / `iss` required
- [ ] Access JWT not stored in `localStorage`
- [ ] Refresh never in JSON
- [ ] CSRF on cookie mutations
- [ ] Generic 401 text (anti-enumeration)
- [ ] Inactive → 403 after successful authn
- [ ] Role from DB, not from JWT
- [ ] Service-layer ownership remains the IDOR control
- [ ] No control-plane secrets in sandbox (out of this sprint, do not regress)
- [ ] Do not advertise instant access-token revoke until a denylist exists

---

## Verification bar

- Backend pytest all green (expect ~28–35 tests after the new matrix, not the Phase 2 “50+” goal — that is Sprint 4)
- Frontend vitest all green, including refresh + CSRF header
- Targeted Ruff on changed files
- No python-jose import left in `prime_agent/` or `tests/`

---

## Implementation order

1. Task 3.1 PyJWT + JWTManager (foundation; fail the suite if claims regress)
2. Task 3.4 first JWT/dep tests (red/green around 3.2)
3. Task 3.2 Annotated consolidation + admin gate
4. Task 3.3 CSRF + frontend
5. Task 3.5 hygiene
6. Write `SPRINT3-COMPLETION-SUMMARY.md` only after the verification bar

Do not start Sprint 4 (broad service tests) until this bar is green.

---

## Success definition

Sprint 3 is complete when:

1. There is a single JWT claim policy module using PyJWT
2. Routes share one `CurrentUser` alias
3. Role dependencies are tested and used
4. Cookie refresh is CSRF-protected
5. Identity still never leaves the control plane
6. Sprint 2 owner-scoping and refresh rotation still pass
