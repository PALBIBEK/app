# Phase 2 Sprint 6 Plan — D1 Service Auth Vertical

**Date:** 2026-08-29  
**Status:** Complete — see `SPRINT6-COMPLETION-SUMMARY.md`  
**Goal:** System BFF is the only trusted AI caller; supervisor rejects anonymous and cross-tenant spoofing.

Source of truth: `prime-agent-study-main/development/ai-backend-production-grade/D1.md`  
Patterns: `agent-platform-research-insight/PYTHON_PATTERNS_MASTERCLASS.md` (§7 DTO, §11 Service, §6 Factory, §10 Errors, §12 Testing)

## Slice (this sprint only)

1. `contracts/service_auth.md` — threat freeze + claim contract
2. System: AI settings, `TenantPrincipal` DTO, service JWT minter, `AiClient`, `GET /api/v1/ai/health`
3. Supervisor: `AUTH_REQUIRED`, service JWT verify, claims-only tenant/principal, conflicting `X-*` → 403
4. E2E tests T1–T5 + boot-fail + no user-JWT leakage

## Out of scope

- Gateway / memory fail-closed (D1 Acc C/D)
- D2 platform status aggregate
- D4 sessions UI
- Frontend AI URLs (`NEXT_PUBLIC_*`)

## Residual

Document if shared-secret fallback remains; prefer JWT-only for authenticated callers.
