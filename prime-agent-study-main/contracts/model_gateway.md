# Model Gateway (normative)

Sibling deployable: `YAL_AGENT_SERVICE/agent-platform/services/model-gateway/`.

## Independence

- Own package, deps, tests, Dockerfile, README.
- **MUST NOT** import `supervisor.*`.
- Consumers depend on **OpenAI-compat HTTP** + optional `ModelGatewayClient`
  Protocol — not on gateway Python modules.
- A developer can `cd services/model-gateway && pytest` without supervisor.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/healthz` | Liveness (process only; no deps) |
| GET | `/v1/readyz` | Readiness — see below |
| GET | `/v1/routes` | Route catalog (no secrets) |
| POST | `/v1/chat/completions` | OpenAI-compat; `model` = **route id** |

### Readiness (`GET /v1/readyz`)

Public probe. **200** `{"status":"ready"}` when:

1. Settings load and `routes()` is non-empty
2. Every route’s `provider` is registered
3. Non-`echo` providers have required API key env non-empty (`OPENROUTER_API_KEY` / `OPENAI_API_KEY`)
4. When `AUTH_REQUIRED` or `ENV=production|staging`, `MODEL_GATEWAY_TOKEN` is non-empty

Else **503** `{"status":"not_ready","reason":"<enum>"}` with reason one of:
`routes` | `provider` | `credentials` | `auth`.

Echo-only route profiles are ready without vendor API keys. `/v1/healthz` stays process-only.

Optional: `Authorization: Bearer <MODEL_GATEWAY_TOKEN>` (service), or
`Authorization: Bearer vk_…` (session virtual key, WP-M5).

**Production / staging:** `MODEL_GATEWAY_TOKEN` **MUST** be set (or
`AUTH_REQUIRED=true` / `ENV=production|staging`). Empty token → fail-closed
(`AUTH_UNCONFIGURED` 503 on protected routes). Compare Bearer with
constant-time equality. See `contracts/service_auth.md`.

Fail-loud: `ROUTE_UNKNOWN` (404), `AUTH_*` (401), `VIRTUAL_KEY_*` (403),
`BAD_ARGS` (400), `PROVIDER_MISSING` / `UPSTREAM_ERROR`.

## Streaming

`POST /v1/chat/completions` with `"stream": true`:

- **echo** — synthetic OpenAI-compat SSE (`data:` chunks + `[DONE]`).
- **openai** / **openrouter** — upstream `stream=true` SSE **relay** (bytes
  forwarded; not 501). Keys stay in the gateway process.
- Consumers (e.g. supervisor `HttpModelGatewayClient`) parse `data:` lines.

### Product Test chat profile (D5)

**Shipped product path:** Githi / Test chat uses gateway **`complete`** (non-stream)
and surfaces assistant text via journal/SSE **`message_end`** (authoritative).
Token-delta streaming to the browser is optional; gateway + supervisor **do**
support `stream: true` for providers and for future UI chunks. D5 Done does
**not** require live token SSE in the Test page when `message_end` delivers
visible text.

## Metering (usage + cost)

Every non-stream completion **MUST** include normalized `usage`:

| Field | Meaning |
|---|---|
| `prompt_tokens` / `input_tokens` | Mirrored |
| `completion_tokens` / `output_tokens` | Mirrored |
| `cost` / `cost_total` | Same USD float — supervisor/githi reads either |

Sources:

1. Upstream `usage.cost` / `usage.cost_total` when present (e.g. OpenRouter).
2. Else a token-based pricing **estimate** (gateway-local; not a bill).
3. **echo** invents a small positive cost so CI budgets move.

## Virtual keys (WP-M5)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/v1/virtual-keys` | service token only | Mint session-scoped key |
| POST | `/v1/virtual-keys/revoke` | service token only | By `session_id` or `token` |

Claims: `tenant_id`, `session_id`, `principal_id`, `allowed_models[]`,
`max_budget_usd`, `expires_at`. Provider secrets stay in gateway env only.

### VK budget enforcement

Gateway **tracks spend** per virtual key (sum of completion `usage.cost_total`).
When `spent_usd >= max_budget_usd`, further chat with that VK returns
**403** `{ "error": { "code": "VIRTUAL_KEY_BUDGET_EXCEEDED", … } }`.
Service-token chat is not budget-capped.

## Retries / fallback

Route meta (in `MODEL_GATEWAY_ROUTES_JSON`) may include:

```json
{
  "githi": {
    "provider": "openrouter",
    "upstream_model": "anthropic/claude-sonnet-4",
    "retries": 1,
    "fallback_route_ids": ["githi-backup"]
  }
}
```

- `retries` — additional attempts after the first on **transient HTTP 429**
  (default **1** = retry once). Documented + covered by mock-transport tests.
- `fallback_route_ids` — if the primary provider still fails, try each listed
  route id once (simple chain; not a full LiteLLM graph).

## Supervisor coupling

- Protocol: `ModelGatewayClient` (`contracts/seams.md`)
- HTTP adapter: `HttpModelGatewayClient` (factory-only concrete)
- Config: `GATEWAY_BASE_URL` (+ `GATEWAY_TOKEN`)

## Related rules

G-1 … G-4, H-4, S-1, T-1, D-4 in `pluggability-rules/dev-rules.md`.
