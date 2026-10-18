# YAL Agent Platform — frontend

Production console monorepo. Phase 0 scaffold + Phase 1 foundation (tokens, core, primitives, shell).

IA prototype: [`prototype.html`](./prototype.html) (**DEPRECATED — IA reference only**).

## Layout

```
packages/
  tokens/     # Style Dictionary → dist/tokens.{css,ts,json}
  core/       # transport, AppError, can(), i18n, format (NO React)
  ui/         # Radix + CSS Modules primitives
  features/   # empty until Phase 2
  app-web/    # Vite + TanStack Router shell
contracts/    # OpenAPI snapshot docs
```

## Same-origin API strategy

Browser always uses **relative** `/api/v1/...`. Vite proxies `/api` → supervisor `:8080` (strips `/api`).

## Commands

```bash
corepack enable
pnpm install
pnpm run check:node
pnpm --filter @yal/tokens build
pnpm run test          # phase0 + phase1 acceptance
pnpm run typecheck
pnpm run lint
pnpm run dev           # http://127.0.0.1:5173 → /default/runs
pnpm --filter @yal/ui ladle
```

Kitchen sink (dev only): `/default/dev/kitchen-sink`

Research answers: `agent-platform-research/frontend/Phase0_frontend.md`, `Phase1_frontend.md`.
