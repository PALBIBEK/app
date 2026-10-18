# Frontend API contracts

**Source of truth:** FastAPI OpenAPI emitted by the supervisor (`/openapi.json` on the supervisor process; browser path via same-origin proxy: `/api/openapi.json`).

**Committed snapshot (Phase 2+):** `contracts/openapi.json` — checked in so CI can generate TypeScript clients without a live backend.

## Generate types (planned command)

```bash
# From agent-platform/frontend once openapi-typescript (or equivalent) is added in Phase 2:
pnpm exec openapi-typescript ./contracts/openapi.json -o packages/core/src/resources/generated.ts
```

Until a reviewed snapshot exists:

1. Capture: `curl -sf http://127.0.0.1:8080/openapi.json -o contracts/openapi.json`
2. Review for loose `any` / missing error schemas before treating as binding.
3. Frontend generation must **fail CI on diff** of generated output (see Phase0 answers P0.3 Q11).

Fallback: hand-written types in `packages/core/src/resources/` headed  
`// TEMPORARY — replace with generated types` with a tracking issue that blocks Phase 3 exit.

**Path mapping (UI ↔ supervisor):** UI “Runs” → `/v1/sessions`; SSE → `GET /v1/sessions/{id}/events`. Full note: Desktop `agent-platform-research/frontend/backend-reality.md`.
