# Phase 2 Sprint 5 Plan — Frontend–Backend Integration

**Date:** 2026-08-29  
**Status:** Complete — see `SPRINT5-COMPLETION-SUMMARY.md`  
**Goal:** A user can register, log in, create a harness, and see it on the dashboard against the live API.

Do not rebuild CORS, JWT, or CSRF. Do not copy the roadmap base URL that omits `/api/v1`.

## Already done (Sprint 2–4)

- Backend `/api/v1`, cookie refresh, CSRF JSON echo
- Axios `withCredentials`, in-memory access token, CSRF header on refresh/logout
- CORS origins for `localhost:3000`

## Must ship

1. `.env.local` → `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` (no token keys in localStorage)
2. Login and register pages calling `setAuth(user, access_token, csrf_token)`
3. Silent session restore: CSRF in `sessionStorage` only; refresh cookie remains HttpOnly
4. Harness API client parses flat backend JSON; update uses PUT
5. Create-harness form posts `chat`/`agent`/`completion` models the API accepts
6. Dashboard lists API harnesses; unauthenticated users go to `/login`
7. Harness detail page uses live API + `AuthGuard` (not mock data)
8. ASGI/integration smoke for CORS + auth + harness happy path

## Out of scope

- Playwright E2E suite
- Forgot-password backend
- Framer Motion `Variants` cleanup on unrelated pages
- Sandbox / supervisor services
