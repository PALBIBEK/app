# YAL deploy artifacts

## Phase 1 (operational baseline)

| Artifact | Purpose |
|---|---|
| `staging/docker-compose.staging.yml` | Postgres + 2 supervisor replicas + model-gateway + **memory :8092** (JWT-first) |
| `../docker-compose.yml` | Local root stack including Memory + `MEMORY_SERVICE_URL` |
| `k8s/supervisor-deployment.yaml` | HA deployment with preStop + readiness |
| `k8s/networkpolicy-sandbox-default-deny.yaml` | Default-deny egress template for sandboxes |
| `k8s/external-secret-gateway.yaml` | ESO template for gateway/DB secrets |
| `../docs/cold-resume-slo.md` | Cold-resume SLO and routing options |

### Staging

```bash
# from repo root
docker compose -f deploy/staging/docker-compose.staging.yml up --build
# supervisor-a → :8080, supervisor-b → :8081, postgres → :5432
# model-gateway → :8090 (MODEL_GATEWAY_TOKEN defaults to staging-gateway-token)
# memory → :8092 (SERVICE_JWT_SECRET; MEMORY_ALLOW_STATIC_TOKEN unset)
```

Set `IDLE_REAP_MAX_SECONDS=3600` (default in compose) to enable background idle reaper.

**Memory auth (JWT-first):** supervisors mint short-lived Memory JWTs from
`SERVICE_JWT_SECRET`. Staging does **not** set `MEMORY_ALLOW_STATIC_TOKEN`, so a
static `MEMORY_SERVICE_TOKEN` is not accepted. `/v1/readyz` is public — compose
health probes need no bearer. For CI residual only:
`MEMORY_ALLOW_STATIC_TOKEN=1` + `MEMORY_SERVICE_TOKEN=…`.

**Gateway vendor keys (optional — gateway container only):**

```bash
# Echo-only by default (CI / bring-up; /v1/readyz green without keys).
# With a key set, the gateway auto-registers `githi` (OpenRouter) or `openai`:
export OPENROUTER_API_KEY=sk-or-v1-...
export MODEL_GATEWAY_DEFAULT_ROUTE=githi   # optional: real chat by default
docker compose -f deploy/staging/docker-compose.staging.yml up --build
```

| Env | Where | Notes |
|---|---|---|
| `MODEL_GATEWAY_TOKEN` | gateway + supervisor `GATEWAY_TOKEN` | Non-empty default in staging compose |
| `OPENROUTER_API_KEY` | gateway only | Enables `githi` route when set |
| `OPENAI_API_KEY` | gateway only | Enables `openai` route when set |

Never put these in `NEXT_PUBLIC_*`, the browser Settings UI, or sandbox env.
Kubernetes: template keys live in `k8s/external-secret-gateway.yaml` (ESO →
`yal-gateway-credentials`, consumed by `k8s/gateway-memory-deployment.yaml`).

### Sandbox provider (D10)

| Env | Default (compose) | Notes |
|---|---|---|
| `SANDBOX_PROVIDER` | `local_docker` | Root + staging compose set `SANDBOX_PROVIDER:-local_docker` on supervisor. githi compositions pin the `githi-docker-v1` sandbox profile. |
| `MULTI_TENANT_STRICT=true` | off | **Required for shared prod:** refuses `local_process` / `local_docker` at boot; use **`e2b`** or `none`. |

**E2B network/egress (multi-tenant path):** E2B sandboxes run on E2B cloud VMs with SaaS-managed egress. Supervisor never injects vendor API keys or `GATEWAY_TOKEN` into sandbox env — only optional `vk_*` for agent→gateway. Operators must configure E2B account/firewall policy to allow DNS + model-gateway egress only. See `contracts/sandbox_profile.md` § Provider network / egress.

**Docker hardening:** `local_docker` uses `network_mode=none`, `cap_drop=ALL`, mem/CPU limits. For controlled egress in K8s, apply `k8s/networkpolicy-sandbox-default-deny.yaml` in the sandbox namespace.

### Phase 2 multi-tenant guardrails (optional env)

| Variable | Purpose |
|---|---|
| `MULTI_TENANT_STRICT=true` | Refuse `local_docker` / `local_process` at boot |
| `TENANT_MAX_SESSIONS` | Per-tenant active session cap |
| `TENANT_MAX_SANDBOXES` | Per-tenant live sandbox cap |
| `GLOBAL_MAX_SESSIONS` | Fleet-wide active session cap |
| `TENANT_MAX_COST_PER_TENANT` | Aggregate cost cap (session_index sum) |
| `TENANT_RATE_LIMIT_PER_MINUTE` | Per-tenant HTTP rate limit |

Metrics snapshot: `GET /metrics` on each supervisor replica.

### Metrics

```bash
python services/supervisor/scripts/measure_sandbox_metrics.py --image <digest-pinned-harness>
```

Paste output into `services/supervisor/ARCHITECTURE.md` Step 9 table.
