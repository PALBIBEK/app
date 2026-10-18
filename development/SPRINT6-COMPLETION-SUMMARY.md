# Phase 2 Sprint 6 Completion Summary

**Date:** 2026-08-29  
**Status:** Complete  
**Goal:** D1 System↔Supervisor service-auth vertical (claims-based identity)

Source: `prime-agent-study-main/development/ai-backend-production-grade/D1.md`  
Patterns: `PYTHON_PATTERNS_MASTERCLASS.md` (§7 DTO, §6 Factory, §11 Service, §10 Errors, §12 Testing)

## Shipped

### Contract
- `prime-agent-study-main/contracts/service_auth.md` — trust map + claim contract

### System BFF (`backend/prime_agent/`)
- AI settings + prod validators (`AI_SUPERVISOR_BASE_URL`, `AI_SERVICE_JWT_SECRET` ≠ `SECRET_KEY`)
- `TenantPrincipal` DTO + MVP mapper (`user.id`)
- `ServiceJwtMinter` (`aud=yal-supervisor`, `type=service`)
- `AiClient` (httpx; service Bearer only; no `X-*` identity headers)
- `GET /api/v1/ai/health` gated by `CurrentUser`

### Supervisor
- `AUTH_REQUIRED` / staging|production fail-closed boot
- `ServiceJwtVerifier` + claim-based `current_tenant` / `current_principal`
- Conflicting `X-Tenant-Id` / `X-Principal-Id` → **403**
- Legacy `AUTH_TOKEN` fallback with length-safe compare; `/healthz` stays public
- `PyJWT` dependency added

### Frontend
- `.env.example` forbids `NEXT_PUBLIC_*` AI URLs/tokens

## Verification (E2E)

| Suite | Result |
|-------|--------|
| Backend full | **96 passed** |
| Supervisor `test_service_auth` + `test_sessions_api` | **16 passed** |
| Cross-plane `test_d1_e2e_service_jwt` | System-minted JWT creates supervisor session |
| Matrix T1–T5 | Covered (401 anon, spoof 403, user JWT 401, BFF 401 before upstream, service Bearer only) |

## VibeSec

Pass on claim isolation, no user-JWT forward, no service token in browser JSON, separate secrets, no NEXT_PUBLIC AI secrets. Fixed legacy `compare_digest` length mismatch → always 401.

## Residual / next

- Gateway + memory fail-closed (D1 Acc C/D) — deferred
- Prefer JWT-only in prod (drop `AUTH_TOKEN` header trust)
- **D2** — platform ready gate using `AiClient.readyz`
