# YAL Model Gateway

Independent OpenAI-compat model access service. Develop and test this package
**alone** — no supervisor DB, catalog, or harness imports required.

## Config

| Env | Default | Meaning |
|---|---|---|
| `MODEL_GATEWAY_TOKEN` | empty | Shared service Bearer. **Required** when `AUTH_REQUIRED=true` or `ENV=production\|staging` (fail-closed). Empty only for local/dev. |
| `AUTH_REQUIRED` | `false` | Force fail-closed auth even outside prod/staging |
| `ENV` | empty | `production` / `staging` → auth required |
| `MODEL_GATEWAY_DEFAULT_ROUTE` | `echo` | Route id when request omits `model` / route header |
| `MODEL_GATEWAY_ROUTES_JSON` | see below | JSON map `route_id → {provider, upstream_model, …}` |
| `OPENAI_API_KEY` | empty | Vendor key for `openai` provider (gateway process only; never harness / `NEXT_PUBLIC_*`) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Upstream OpenAI-compat base |
| `OPENROUTER_API_KEY` | empty | Vendor key for `openrouter` provider (`githi` route) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter OpenAI-compat base |
| `PORT` | `8090` | Listen port |

Default routes (when `MODEL_GATEWAY_ROUTES_JSON` unset):

```json
{
  "echo": {"provider": "echo", "upstream_model": "echo"},
  "fake": {"provider": "echo", "upstream_model": "fake"},
  "githi": {"provider": "openrouter", "upstream_model": "anthropic/claude-sonnet-4"}
}
```

**Echo-only + vendor keys:** Staging compose pins an echo-only
`MODEL_GATEWAY_ROUTES_JSON` so `/v1/readyz` is green in CI. If
`OPENROUTER_API_KEY` / `OPENAI_API_KEY` is later set, the gateway
**auto-registers** `githi` / `openai` (see `Settings.routes`). Set
`MODEL_GATEWAY_DEFAULT_ROUTE=githi` when you want real chat by default.

## Auth (prod / staging)

- Protected routes require `Authorization: Bearer <MODEL_GATEWAY_TOKEN>` or a
  live `vk_…` virtual key.
- Empty token under prod/staging / `AUTH_REQUIRED` → **503** `AUTH_UNCONFIGURED`
  (and `/v1/readyz` reason `auth`). Compare with constant-time equality.
- Local/dev may omit the token (auth off) only when not in those profiles.

## Streaming

- `stream: false` — JSON completion (echo, openai, openrouter).
- `stream: true` — `text/event-stream` for **echo** (synthetic) and for
  **openai** / **openrouter** (upstream SSE relay; not 501).

## Metering

- Completions normalize `usage` with `prompt_tokens` / `completion_tokens` and
  supervisor-readable `cost` / `cost_total` (same USD float).
- Prefer upstream cost; else token pricing estimate. Echo invents a small cost.

## Virtual keys + budget

- `POST /v1/virtual-keys` (service token) mints session `vk_…` with
  `allowed_models`, `max_budget_usd`, expiry.
- Chat accepts `Bearer vk_…`; allowlist / expiry / revoke are enforced.
- **`max_budget_usd` is enforced:** gateway accumulates spend from
  `usage.cost_total`. When `spent >= max_budget_usd` → **403**
  `VIRTUAL_KEY_BUDGET_EXCEEDED`.

## Retries / fallback

Route meta may include `retries` (extra attempts on HTTP 429; default 1) and
`fallback_route_ids` (try alternate route ids after primary failure).

## Semantics

- `GET /v1/healthz` — liveness
- `GET /v1/readyz` — routes + providers + non-echo credentials + auth (prod/staging)
- `GET /v1/routes` — configured route ids (no secrets)
- `POST /v1/chat/completions` — OpenAI-compat; `model` field = **route id** (G-2)
- Optional header `X-YAL-Route-Id` overrides body `model`
- Auth: when configured, missing/wrong Bearer → **401** / **503**
- Unknown route → **404** with `{ "error": { "code": "ROUTE_UNKNOWN", … } }`

Providers:

- `echo` — deterministic assistant reply (local CI / solo development)
- `openai` — forward to OpenAI-compat upstream using gateway-held keys
- `openrouter` — forward to OpenRouter (used by the `githi` route)

## Staging / local non-echo Test (D5 E1)

Vendor keys (`OPENROUTER_API_KEY` / `OPENAI_API_KEY`) live **only** in the gateway
container. To make supervisor session pins use `githi` instead of catalog `echo`:

```bash
export OPENROUTER_API_KEY=sk-or-...
export GATEWAY_VENDOR_READY=1          # or YAL_MODEL_ROUTE=githi
export MODEL_GATEWAY_DEFAULT_ROUTE=githi   # optional; mirror on gateway + supervisor
docker compose up --build
```

Do **not** copy vendor keys onto the supervisor service.

## Limits (MVP)

- Not a full LiteLLM product (no billing UI, complex fallback graphs)
- Does **not** import `supervisor.*`

## Extension points

- Add a provider under `model_gateway/providers/` and register in `factory.py`
- Point supervisor at this service via `GATEWAY_BASE_URL` + `HttpModelGatewayClient`
- Swap routes via env without rebuilding consumers

## Run alone

```bash
cd services/model-gateway
pip install -e ".[dev]"
uvicorn model_gateway.app:app --port 8090
pytest
```

## Staging vendor keys

See `deploy/staging/docker-compose.staging.yml` and `deploy/README.md`.
Keys enter via host env / ESO (`deploy/k8s/external-secret-gateway.yaml`) into
the gateway pod only — never the browser Settings page or sandbox env.
