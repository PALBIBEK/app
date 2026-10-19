# Model routing (normative)

How the supervisor reaches models: QM's model layer, ported from YC's
[QM](https://github.com/yc-software/qm) (MIT) — `src/model/gateway-catalog.ts`,
`gateway-models.ts`, `provider-endpoints.ts`, `pi-models.ts`,
`browser-completion.ts` and the pi-ai transports they drive. Code:
`services/supervisor/src/supervisor/models/`.

## Configuration

| Env | Meaning |
|---|---|
| `MODEL_GATEWAY_URL` | LiteLLM-compatible gateway base URL, with or without `/v1`. http(s) only; no credentials, query or fragment; trailing slashes dropped. |
| `MODEL_GATEWAY_API_KEY` | The gateway key. Stays in the supervisor; used for discovery, and for model calls when a session has no virtual key. |
| `MODEL_GATEWAY_API_KEY_HEADER` | Header the key is sent in (e.g. `x-litellm-api-key`). Must be an HTTP token. |
| `MODEL_GATEWAY_MODELS` | Optional `native-id=gateway-group` pairs, comma separated: serve QM's native model definitions through the gateway. |

Setting any of them requires the first three; an invalid value, a duplicate
alias source or an alias naming a model the native registry does not define
**fails boot**. Unset, sessions keep the chat-completions client on
`GATEWAY_BASE_URL`.

A composition that pins `plug_bindings.gateway_base_url` keeps its pin: it is
routed only when the pinned URL names the configured gateway (same origin and
path, `/v1` or not); a different pinned gateway gets the chat-completions client.

## Discovery

The catalog reads `GET {root}/v1/models` and `GET {root}/model_group/info` with
the gateway key: 5 s timeout, no redirects, at most 2 MiB and 1000 entries per
response. A group is offered only when both list it, it is `mode: chat` with
`supports_function_calling: true`, and it has positive `max_input_tokens` and
`max_output_tokens` (output smaller than input) and non-negative per-token
prices. Embedding groups are kept (and never chat). Discovered ids are
`gateway/<group>`; upstream URL and credential fields are ignored.

| Providers advertised | Wire | Notes |
|---|---|---|
| only `anthropic` | Anthropic Messages at `{root}` | signed thinking replayed; adaptive thinking when `supports_adaptive_thinking` |
| only `openai` / `azure` | OpenAI Responses at `{root}/v1` | stateless (`store: false`), encrypted reasoning replayed |
| anything else | chat completions at `{root}/v1` | `max_completion_tokens` only when advertised |

Each spec also records its document support as QM derives it (`files` for
OpenAI/Azure groups; `pdf` when every provider is Anthropic, OpenAI, Azure,
Gemini or Vertex); sessions send no documents yet.

Reasoning controls exist only for native Anthropic and OpenAI/Azure groups with
`supports_reasoning`. Every gateway model sends session-affinity headers
(Messages `x-session-affinity`; Responses `session_id` + `x-client-request-id`;
chat completions all three). Missing cache prices take the input price.

**Lifecycle.** Refresh on demand: at most every 5 minutes, and before every
model call once expired; one refresh at a time. A successful refresh replaces
the catalog, empty or not. A failed one retries after 30 s; after a first
success, a *transient* failure (429, 5xx, timeout, connection error, redirect)
keeps the last catalog for up to 15 minutes, anything else — or a longer outage
— disables gateway models until recovery. Aliases are routable before the first
successful discovery, and afterwards only while their target group is listed;
a successful discovery that drops them is never undone by a later failure.

## Model ids

A session's model (`model_route` pin, else the manifest's model) resolves to:

1. `gateway/<group>` — a discovered group;
2. a native registry id — QM's list on pi-ai's templates (Claude Opus/Sonnet/
   Haiku/Fable 5.x, GPT-5.6/6/6.1, OpenRouter Auto), routed when aliased;
3. a bare group (a legacy route id such as `githi`) — as `gateway/<group>`.

A gateway model or a reserved alias that the gateway does not route raises
`GatewayModelUnavailable` (404, `model_unavailable`) — never a silent fallback
to another provider. A native model that is not aliased is unavailable: model
keys live in the gateway.

**Ultrafast.** `gpt-6-astra-ultrafast` and `gpt-6.1-sol-ultrafast` are
selections of their base models: requests name the base group with
`service_tier: "ultrafast"` on the Responses wire, priced on their own 6× card
(cached input, cache writes and the >272k tier included). Flex and Priority
tiers reprice a Responses call (0.5×; 2×, 2.5× for GPT-5.5).

**Effort.** Sessions run at `auto`: low for Anthropic models, medium otherwise,
clamped to the levels the model supports (`xhigh`/`max` only where mapped).

## Replay across models

An assistant turn is journalled as ordered parts — `text` (with the Responses
message id as its signature), `reasoning` (the Anthropic signature or the
Responses reasoning item, plus the `api` and `model` that produced it) and
`tool_call`. Native reasoning returns only to the model that produced it; for
any other model it becomes plain text (opaque redacted thinking is dropped),
tool call ids are re-spelled for the target protocol, a tool call without a
result gets a synthetic error result, and a failed turn is not replayed.

## Session keys

With a virtual-key service configured, each session's key is minted for the
gateway group its model is served by (an alias's target, a group's own id) and
is the key its model calls carry; budget exhaustion is a non-retryable 429.

## Browser model endpoint

`POST /v1/browser-model/chat/completions` — the sandboxed browse runner's model
access, without a model key.

- Each tool call of a non-strict session's turn gets `BROWSE_LAB_MODEL_PROVIDER=managed`,
  `BROWSE_LAB_MODEL`, `BROWSE_LAB_BASE_URL` (`$SUPERVISOR_PUBLIC_URL/v1/browser-model`)
  and `BROWSE_LAB_MODEL_TOKEN`: an HS256 capability (`aud: browser-model`, one
  hour) bound to the session, tenant, owner and model, signed with
  `CAPABILITY_SECRET` (comma-separated for rotation; falls back to
  `SERVICE_JWT_SECRET`). The sandbox env filter admits that token and nothing
  else secret.
- Requests: up to 16 MiB; non-streaming; `model` must be the granted model; only
  `model, messages, max_tokens, max_completion_tokens, temperature, top_p, seed,
  stop, response_format, reasoning_effort, tools, tool_choice,
  parallel_tool_calls, verbosity, stream` — anything else (a provider override)
  is 400.
- Checks, per request: a valid capability (403), a non-strict session (403 —
  strict = pinned read-only sandbox), the same tenant, and that the session
  still selects the granted model (409: start a new turn).
- A model that needs a native protocol (Ultrafast) runs through the routed
  transports: screenshots (inline PNG/JPEG/WebP/GIF data URLs, user turns only)
  become image inputs, a JSON-schema `response_format` becomes a forced
  `browser_result` tool call, tools are refused. Any other model is relayed to
  the gateway's chat completions for its group (503 if unrouted).
- Upstream refusals map to 429 (rate or budget) or 502. Calls carry the
  session's own gateway key, so its permissions and budget apply.

## Supervisor API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/models` | Selectable models (discovered groups not shadowed by an alias, then aliased native models): protocol, limits, prices with tiers, thinking levels. No endpoints or keys. |
| POST | `/v1/browser-model/chat/completions` | Above. |

## Not ported

Direct provider keys in the supervisor (and QM's `*_BASE_URL` overrides for
them), per-user subscription accounts (ChatGPT, Claude) and custom provider
overlays: keys stay in the gateway, and accounts are the gateway's virtual keys.
The model-call audit is QM's per-process ring buffer; the journal is the durable
record.
