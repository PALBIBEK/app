# Model Gateway (normative)

Sibling deployable: `YAL_AGENT_SERVICE/agent-platform/services/model-gateway/`.

## Independence

- Own package, deps, tests, Dockerfile, README.
- **MUST NOT** import `supervisor.*`.
- Consumers depend on its **HTTP contract** — LiteLLM's end-user surface
  (discovery, chat completions, Anthropic Messages, OpenAI Responses) — plus the
  optional `ModelGatewayClient` Protocol, never on gateway Python modules. Any
  LiteLLM-compatible gateway can stand in for it (`contracts/model_routing.md`).
- A developer can `cd services/model-gateway && pytest` without supervisor.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/healthz` | Liveness (process only; no deps) |
| GET | `/v1/readyz` | Readiness — see below |
| GET | `/v1/routes` | Route catalog (no secrets): `id`, `provider`, `upstream_model`, `context_window` |
| GET | `/v1/routes/{route_id}` | One route (same fields); `ROUTE_UNKNOWN` 404. Supervisor: `ModelCatalog.context_window` |
| GET | `/v1/models` | OpenAI model list: the route ids the caller may call |
| GET | `/model_group/info` | LiteLLM end-user model-group metadata for those routes (below) |
| POST | `/v1/chat/completions` | OpenAI chat completions; `model` = **route id** |
| POST | `/v1/messages` | Anthropic Messages passthrough (routes whose provider serves it) |
| POST | `/v1/responses` | OpenAI Responses passthrough (routes whose provider serves it) |

The three inference wires are **passthroughs**: the caller's body goes upstream
with only `model` rewritten to the route's `upstream_model` (and `stream` made
explicit), so provider-native fields survive — signed `thinking` blocks,
`reasoning` items with `encrypted_content`, `service_tier`, `output_config`,
`cache_control`. A route whose provider does not serve a wire answers
**400** `WIRE_UNSUPPORTED`; an embedding route on an inference wire **400**
`ROUTE_MODE`.

| Provider | Wires | Credential |
|---|---|---|
| `echo` | all three (network-free, deterministic) | none |
| `openai` | chat completions, Responses | `OPENAI_API_KEY` (`OPENAI_BASE_URL`) |
| `openrouter` | chat completions | `OPENROUTER_API_KEY` (`OPENROUTER_BASE_URL`) |
| `anthropic` | Messages, chat completions (its OpenAI-compatible endpoint) | `ANTHROPIC_API_KEY` (`ANTHROPIC_BASE_URL`, no `/v1`) |

**Echo is as strict as the vendors about replayed state:** with thinking
enabled it signs a `thinking` block and rejects (400) a replayed one whose
signature does not verify; asked for `reasoning.encrypted_content` it returns a
reasoning item and rejects replayed reasoning without valid encrypted content.
It answers `echo:<last user text or tool result>`; a user turn
`/tool <name> <json>` naming an offered tool, or a `tool_choice` forcing one,
produces a tool call. `service_tier` is echoed on Responses.

### Model-group metadata (`GET /model_group/info`)

`{"data": [...]}`, one row per route the caller may call:

| Field | From the route |
|---|---|
| `model_group` | route id |
| `mode` | `chat` (default) or `embedding` |
| `providers` | `providers` (LiteLLM provider labels), default `[provider]` |
| `max_input_tokens` | `context_window` |
| `max_output_tokens` | `max_output_tokens` |
| `input_cost_per_token`, `output_cost_per_token` | USD per token |
| `cache_read_input_token_cost`, `cache_creation_input_token_cost` | USD per token (optional) |
| `supports_function_calling` | default `true` |
| `supports_vision`, `supports_reasoning`, `supports_adaptive_thinking` | default `false` |
| `supported_openai_params` | e.g. `["reasoning_effort", "max_completion_tokens"]` |

Clients choose the wire from `providers` — never from the name: only
`anthropic` → Messages; only `openai`/`azure` → Responses; else chat
completions. No upstream URL or credential field is ever returned.

A route is a typed record (unknown fields rejected). An invalid
`MODEL_GATEWAY_ROUTES_JSON` fails closed: `/v1/readyz` reason `routes`, every
route-dependent call **500** `ROUTES_INVALID` naming the field.

### Readiness (`GET /v1/readyz`)

Public probe. **200** `{"status":"ready"}` when:

1. Settings load and `routes()` is non-empty
2. Every route’s `provider` is registered
3. Non-`echo` providers have required API key env non-empty (`OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`)
4. When `AUTH_REQUIRED` or `ENV=production|staging`, `MODEL_GATEWAY_TOKEN` is non-empty

Else **503** `{"status":"not_ready","reason":"<enum>"}` with reason one of:
`routes` | `provider` | `credentials` | `auth`.

Echo-only route profiles are ready without vendor API keys. `/v1/healthz` stays process-only.

Optional: `Authorization: Bearer <MODEL_GATEWAY_TOKEN>` (service), or
`Authorization: Bearer vk_…` (session virtual key, WP-M5).

The key is read from `MODEL_GATEWAY_API_KEY_HEADER` (default `Authorization`;
e.g. `x-litellm-api-key`) first, then `Authorization`, then `x-api-key`; a
`Bearer ` prefix is optional. A virtual key sees only its `allowed_models` on
`/v1/models` and `/model_group/info`.

**Production / staging:** `MODEL_GATEWAY_TOKEN` **MUST** be set (or
`AUTH_REQUIRED=true` / `ENV=production|staging`). Empty token → fail-closed
(`AUTH_UNCONFIGURED` 503 on protected routes). Compare Bearer with
constant-time equality. See `contracts/service_auth.md`.

Fail-loud: `ROUTE_UNKNOWN` (404), `AUTH_*` (401), `VIRTUAL_KEY_*` (403),
`VIRTUAL_KEY_BUDGET_EXCEEDED` (429), `BAD_ARGS` (400), `WIRE_UNSUPPORTED` /
`ROUTE_MODE` (400), `UPSTREAM_REJECTED` (the upstream's 4xx, relayed),
`ROUTES_INVALID` (500), `PROVIDER_MISSING` (500), `UPSTREAM_ERROR` (502).

## Streaming

Every wire streams with `"stream": true`, in its own event format:

- **echo** — synthetic SSE spelled as the wire would (chat chunks + `[DONE]`;
  Messages `message_start` … `message_stop`; Responses `response.created` …
  `response.completed`).
- **vendor providers** — upstream SSE **relayed** byte for byte. Keys stay in
  the gateway process. Chat streams ask for `stream_options.include_usage`.
- The relay is metered from the wire's own usage events (Messages:
  `message_start` + `message_delta`; Responses: the terminal event; chat: the
  last chunk) and charged to the virtual key when the stream ends.

### Product Test chat profile (D5)

**Shipped product path:** Githi / Test chat uses gateway **`complete`** (non-stream)
and surfaces assistant text via journal/SSE **`message_end`** (authoritative).
Token-delta streaming to the browser is optional; gateway + supervisor **do**
support `stream: true` for providers and for future UI chunks. D5 Done does
**not** require live token SSE in the Test page when `message_end` delivers
visible text.

## Metering (usage + cost)

Each call is priced, in order of preference: the upstream's own cost
(OpenRouter reports one); else the route's per-token prices (cache reads and
writes default to the input price, `input` excludes cached tokens); else a
token estimate. Every non-streaming response carries the price in LiteLLM's
`x-litellm-response-cost` header.

Every non-stream chat completion **MUST** include normalized `usage`:

| Field | Meaning |
|---|---|
| `prompt_tokens` / `input_tokens` | Mirrored |
| `completion_tokens` / `output_tokens` | Mirrored |
| `cost` / `cost_total` | Same USD float — supervisor/githi reads either |

Sources:

1. Upstream `usage.cost` / `usage.cost_total` when present (e.g. OpenRouter).
2. Else the route's prices; else a token-based **estimate** (gateway-local).
3. The default **echo** routes are priced at 0.0005 USD per token so CI budgets move.

## Virtual keys (WP-M5)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/v1/virtual-keys` | service token only | Mint session-scoped key |
| POST | `/v1/virtual-keys/revoke` | service token only | By `session_id` or `token` |

Claims: `tenant_id`, `session_id`, `principal_id`, `allowed_models[]`,
`max_budget_usd`, `expires_at`. Provider secrets stay in gateway env only.

### VK budget enforcement

Gateway **tracks spend** per virtual key (every wire, streaming included).
When `spent_usd >= max_budget_usd`, further calls with that VK return
**429** `{ "error": { "code": "VIRTUAL_KEY_BUDGET_EXCEEDED", … } }` — a quota
condition, as OpenAI's `insufficient_quota`, so QM-style clients report budget
exhaustion as 429. Service-token calls are not budget-capped.

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

- `retries` — additional attempts after the first on a **transient** failure:
  HTTP 408/409/429/5xx, a connection error or a timeout (default **1**), with
  capped exponential backoff honouring `Retry-After`. A stream is retried only
  before its first byte.
- `fallback_route_ids` — if the primary provider still fails transiently, try
  each listed route id once (simple chain; not a full LiteLLM graph). An
  upstream 4xx other than 429 is the caller's to fix: relayed at once, never
  retried or failed over.

When a vendor key is set and the route table lacks that vendor's default route,
the route is added: `githi` (OpenRouter), `gpt-5.5` (OpenAI Responses),
`claude-sonnet-5` (Anthropic Messages, adaptive thinking).

## Supervisor coupling

- Protocol: `ModelGatewayClient` (`contracts/seams.md`)
- Routed adapter (QM's model layer): `RoutedModelClient` — discovery through
  `/v1/models` + `/model_group/info`, then Messages / Responses / chat
  completions per group. Config: `MODEL_GATEWAY_URL`, `MODEL_GATEWAY_API_KEY`,
  `MODEL_GATEWAY_API_KEY_HEADER`, `MODEL_GATEWAY_MODELS`. See
  `contracts/model_routing.md`.
- Chat-completions adapter: `HttpModelGatewayClient` (factory-only concrete).
  Config: `GATEWAY_BASE_URL` (+ `GATEWAY_TOKEN`).

## Related rules

G-1 … G-4, H-4, S-1, T-1, D-4 in `pluggability-rules/dev-rules.md`.
