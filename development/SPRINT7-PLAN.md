# Phase 2 Sprint 7 Plan — D2 Product Ready Gates

**Date:** 2026-08-30  
**Status:** Complete  
 
**Goal:** System refuses Test/send theater when AI is down; ops `/ready` stays system-DB-only.

Sources: `D2.md`, Sprint 6 handoff (`AiClient.readyz`), `PYTHON_PATTERNS_MASTERCLASS.md` (§7 DTO, §11 Service, §10 Errors, §12 Testing).  
Research: three parallel explore agents (D2 recipe, code audit, probe-pattern consensus).

## Slice (this sprint)

1. Extend `AiClient.readyz` to parse supervisor 503 `{status, reason}`
2. `PlatformStatusService` + `GET /api/v1/platform/status` (auth’d, always 200 when logged in)
3. Shared `ensure_ai_platform_ready` → `503 AI_PLATFORM_NOT_READY`
4. Frontend: `usePlatformStatus` + banner + disable Test/send; **no mock success** when `!run_allowed`
5. Prove system `GET /ready` still ignores AI (T9)

## Explicitly out

- Gateway / memory `/v1/readyz` (stub as residual: not probed)
- Supervisor HTTP probes of gateway/memory
- D3 catalog bind, D4 real sessions/SSE
- AI checks inside system `/ready`

## MVP aggregate

`run_allowed` = supervisor `/readyz` ok only. Document residual: gateway/memory not yet in aggregate.

## Test names (must pass)

- `test_platform_status_unauthenticated_returns_401_before_upstream_call`
- `test_platform_status_ai_down_returns_200_ready_false_for_logged_in_user`
- `test_platform_status_ai_ready_returns_200_run_allowed_true`
- `test_platform_status_uses_service_bearer_not_user_access_token`
- `test_ensure_ai_platform_ready_raises_503_ai_platform_not_ready`
- `test_system_ready_still_200_when_ai_supervisor_unreachable`
- `test_ai_client_readyz_parses_503_not_ready_body`
- FE: disable Test/send + skip mock when not ready
