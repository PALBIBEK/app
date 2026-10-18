# Phase 2 Sprint 5 Completion Summary

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** Register, log in, create a harness, and see it on the dashboard / detail page against `/api/v1`

Do **not** use the roadmap base URL that omits `/api/v1`.

## Research applied

- Cookie refresh + CSRF JSON echo (Sprint 2–4) — frontend must send `withCredentials` and `X-CSRF-Token`
- Access token in Zustand memory only; CSRF in `sessionStorage` for reload bootstrap
- Single-flight 401 refresh; skip refresh on login/register/refresh to avoid loops
- CORS: explicit origins + credentials (no `*`)
- VibeSec: AuthGuard on all harness routes; React-escaped fields; ownership stays server-side

## Shipped

### Env / client (already aligned; verified)

- `frontend/.env.local` + `.env.example` → `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`
- Axios `withCredentials`, CSRF on refresh/logout, in-memory access token
- Backend CORS allows `http://localhost:3000` / `3001`

### Product wiring (closed this sprint)

- `/login` + `/register` → `setAuth(user, access_token, csrf_token)`
- `AuthBootstrap` silent restore via refresh cookie + CSRF
- Dashboard lists API harnesses behind `AuthGuard`
- Create form posts valid models + `chat` / `agent` / `completion`
- **`/harness/[id]`** loads/updates/deletes via live API (was mock UI)
- **`/harness/[id]/test`** and **`/sessions`** wrapped in `AuthGuard`
- `useHarness` gated on `isAuthenticated`

### Verification

| Check | Result |
|-------|--------|
| Frontend Vitest (client, auth store, harness hooks/endpoints) | **47 passed** |
| Backend ASGI smoke `tests/test_sprint5_integration_smoke.py` | **pass** (CORS, register, refresh CSRF, create/list/get harness, 401, logout) |
| Live Postgres `uvicorn` + browser E2E | Not run here (no local Postgres); smoke covers the same HTTP contract |

## VibeSec

Pass: no access token in localStorage, CSRF double-submit, credentials CORS, React escaping, server IDOR.  
Fixed: AuthGuard on harness test/sessions mock pages.

## Manual browser (when Postgres is up)

```bash
# backend
cd backend && uv run uvicorn prime_agent.main:app --reload

# frontend
cd frontend && npm run dev
```

Flow: register → create harness → dashboard row → open detail → save/delete.

## Deferred

- Playwright E2E suite
- Forgot/reset password backend
- Wiring mock test/sessions UIs to real session APIs (sandbox/supervisor)
- Framer Motion `Variants` cleanup on unrelated pages
