# YAL Model Gateway

Independently deployable model access service. It serves the LiteLLM end-user
contract a QM-style client discovers and calls — model listing and model-group
metadata, plus chat completions, Anthropic Messages and OpenAI Responses — and
holds every vendor key. Develop and test this package **alone** — no supervisor
DB, catalog, or harness imports required.

## Config

| Env | Default | Meaning |
|---|---|---|
| `MODEL_GATEWAY_TOKEN` | empty | Shared service key. **Required** when `AUTH_REQUIRED=true` or `ENV=production\|staging` (fail-closed). Empty only for local/dev. |
| `MODEL_GATEWAY_VIRTUAL_KEY_SECRET` | derived from `MODEL_GATEWAY_TOKEN` | Signs session virtual keys (32+ characters). Every gateway process needs the same one. |
| `MODEL_GATEWAY_API_KEY_HEADER` | `Authorization` | Header a caller's key arrives in (e.g. `x-litellm-api-key`); `Authorization` and `x-api-key` are also read. `Bearer ` prefix optional. |
| `AUTH_REQUIRED` | `false` | Force fail-closed auth even outside prod/staging |
| `ENV` | empty | `production` / `staging` → auth required |
| `MODEL_GATEWAY_DEFAULT_ROUTE` | `echo` | Route id when a request omits `model` / route header |
| `MODEL_GATEWAY_ROUTES_JSON` | see below | JSON map `route_id → route` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | empty / `https://api.openai.com/v1` | `openai` provider (chat completions + Responses) |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | empty / `https://openrouter.ai/api/v1` | `openrouter` provider (chat completions) |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | empty / `https://api.anthropic.com` | `anthropic` provider (Messages + its OpenAI-compatible chat) |
| `PORT` | `8090` | Listen port |

## Routes

A route is one model group: what it calls and the metadata `/model_group/info`
advertises for it. Unknown fields are rejected; an invalid table fails closed.

```json
{
  "claude": {
    "provider": "anthropic",
    "upstream_model": "claude-sonnet-5",
    "context_window": 1000000,
    "max_output_tokens": 128000,
    "input_cost_per_token": 2e-6,
    "output_cost_per_token": 10e-6,
    "cache_read_input_token_cost": 2e-7,
    "cache_creation_input_token_cost": 2.5e-6,
    "supports_vision": true,
    "supports_reasoning": true,
    "supports_adaptive_thinking": true,
    "supported_openai_params": ["max_tokens", "tools", "tool_choice", "reasoning_effort"],
    "retries": 1,
    "fallback_route_ids": []
  }
}
```

Also: `mode` (`chat` | `embedding`), `providers` (LiteLLM provider labels;
default `[provider]` — clients pick the wire protocol from them), and
`supports_function_calling` (default `true`).

Default routes (when `MODEL_GATEWAY_ROUTES_JSON` is unset): `echo`, `fake`
(echo, priced at 0.0005 USD/token so budgets move) and `githi` (OpenRouter
`anthropic/claude-sonnet-4`). Each vendor key that is set adds its default
route when the table lacks it: `githi` (OpenRouter), `gpt-5.5` (OpenAI),
`claude-sonnet-5` (Anthropic).

## API

- `GET /v1/healthz` — liveness
- `GET /v1/readyz` — routes valid, providers registered, vendor keys set, auth (prod/staging)
- `GET /v1/models` — OpenAI model list of the routes the caller may call
- `GET /model_group/info` — LiteLLM model-group metadata for those routes
- `GET /v1/routes`, `GET /v1/routes/{id}` — route catalog (id, provider, upstream model, window)
- `POST /v1/chat/completions` — OpenAI chat completions; `model` = route id
- `POST /v1/messages` — Anthropic Messages passthrough
- `POST /v1/responses` — OpenAI Responses passthrough
- `POST /v1/virtual-keys`, `POST /v1/virtual-keys/revoke` — session keys (service key only)

Inference bodies are relayed with only `model` rewritten, so signed thinking,
encrypted reasoning and `service_tier` reach the upstream intact. Every wire
streams (`"stream": true`) in its own SSE format. Header `X-YAL-Route-Id`
overrides the body's `model`.

## Providers

| Provider | Wires |
|---|---|
| `echo` | all three — deterministic, network-free, and as strict as the vendors about replayed thinking signatures and encrypted reasoning |
| `openai` | chat completions, Responses |
| `openrouter` | chat completions |
| `anthropic` | Messages, chat completions |

Upstream calls retry transient failures (408/409/429/5xx, connection errors,
timeouts) with backoff honouring `Retry-After`, then fail over to
`fallback_route_ids`. An upstream 4xx other than 429 is relayed as is.

## Metering and keys

Each call is priced from the upstream's figure, else the route's prices, else
an estimate, and the price is returned in `x-litellm-response-cost`. Chat
completions also carry `usage.cost` / `usage.cost_total`. Virtual keys see only
their `allowed_models`, and spend on every wire (streams included) counts
toward their budget; an exhausted budget is **429**
`VIRTUAL_KEY_BUDGET_EXCEEDED`.

A virtual key carries its claims (tenant, session, models, budget, expiry), signed
with the virtual key secret, so a restart or another replica takes the keys sessions
hold. Revocations and per-key spend are kept in the process: after a restart a revoked
key works again until it expires (an hour by default; a terminated session's key is
held only by the supervisor, which forgets it), and the session's cost cap, which the
supervisor enforces, is the bound that holds across restarts. Changing the secret makes
every key unknown (**401** `AUTH_INVALID`): the supervisor then mints a new key and asks
again.

## Staging / local non-echo Test (D5 E1)

Vendor keys live **only** in the gateway container. To make supervisor session
pins use `githi` instead of catalog `echo`:

```bash
export OPENROUTER_API_KEY=sk-or-...
export GATEWAY_VENDOR_READY=1          # or YAL_MODEL_ROUTE=githi
export MODEL_GATEWAY_DEFAULT_ROUTE=githi   # optional; mirror on gateway + supervisor
docker compose up --build
```

To let the supervisor discover this gateway's groups and speak each group's
native protocol, also set on the supervisor `MODEL_GATEWAY_URL`,
`MODEL_GATEWAY_API_KEY` and `MODEL_GATEWAY_API_KEY_HEADER`.

Do **not** copy vendor keys onto the supervisor service.

## Limits

- Not a full LiteLLM product (no billing UI, protocol translation between
  wires, or complex fallback graphs); revocations and per-key spend are per process.
- Imports nothing from the rest of `supervisor` (`tests/gateway` checks it).

## Run alone

```bash
cd services/supervisor
SERVE_SUPERVISOR_API=0 SERVE_MEMORY_SERVICE=0 python -m supervisor.main   # :8090
pytest tests/gateway
```

## Staging vendor keys

See `deploy/staging/docker-compose.staging.yml` and `deploy/README.md`.
Keys enter via host env / ESO (`deploy/k8s/external-secret-gateway.yaml`) into
the gateway pod only — never the browser Settings page or sandbox env.
