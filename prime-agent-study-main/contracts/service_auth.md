# Service Auth Contract (D1)

**Status:** Normative for Sprint 6+  
**Audience:** System BFF (`prime_agent`) and AI plane (supervisor first; gateway/memory later)

## Trust map

| Party | Trust |
|-------|--------|
| Browser / SPA | Untrusted for AI identity |
| Sandbox / agent | Untrusted for authz |
| System backend (BFF) | Trusted mapper + minter of AI credentials |
| Supervisor / gateway / memory | Verify service credentials; never trust raw client headers in prod |

## Hard rules

1. User JWT (`aud=prime-agent-web`, `type=access`) **stops at the system backend**.
2. AI sees only **service** credentials + verified `tenant_id` / `principal_id`.
3. Production / `AUTH_REQUIRED` profiles **fail closed** if the verifier is unset.
4. `/healthz` (and optionally `/readyz`) may stay public; `/v1/*` must not be open when auth is required.
5. Browser must never hold AI service secrets or call supervisor URLs (`NEXT_PUBLIC_*` forbidden for AI tokens/URLs).

## Claim contract (service JWT)

| Claim | Required | Notes |
|-------|----------|--------|
| `iss` | yes | `prime-agent-api` |
| `aud` | yes | `yal-supervisor` (supervisor); later `model-gateway` / `memory-service` |
| `sub` | yes | Service id (e.g. `prime-agent-bff`) |
| `type` | yes | Must be `service` (reject `access`) |
| `tenant_id` | yes | MVP: `user.id` |
| `principal_id` | yes | MVP: `user.id` |
| `exp` / `iat` / `jti` | yes | Short TTL (default 60s) |

Signing: HS256 with dedicated `AI_SERVICE_JWT_SECRET` / `SERVICE_JWT_SECRET` — **not** the user `SECRET_KEY`.

## Header policy after JWT

- `Authorization: Bearer <service JWT>` required on `/v1/*` when auth is required.
- `tenant_id` / `principal_id` taken **only from claims**.
- If `X-Tenant-Id` or `X-Principal-Id` is present and disagrees with claims → **403**.
- Prefer omitting authoritative `X-*` from the BFF once claims are verified.

## MVP tenant mapping

Until org/tenant tables exist: `TenantPrincipal.from_user(user)` sets both ids to `user.id` (single-tenant-per-user).

## Residual risk (shared-secret fallback)

If a static `AUTH_TOKEN` fallback remains: anyone with the secret can still spoof tenants via headers. Prefer JWT-only; network policy is required until JWT is exclusive.
