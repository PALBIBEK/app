# YAL deploy artifacts

## Phase 1 (operational baseline)

| Artifact | Purpose |
|---|---|
| `staging/docker-compose.staging.yml` | Postgres + 2 supervisor replicas + model-gateway + **memory :8092** (JWT-first) |
| `../docker-compose.yml` | Local root stack including Memory + `MEMORY_SERVICE_URL` |
| `k8s/supervisor-deployment.yaml` | HA deployment with preStop + readiness |
| `k8s/networkpolicy-sandbox-default-deny.yaml` | Default-deny egress template for sandboxes |
| `k8s/external-secret-gateway.yaml` | ESO templates: one secret per service, provider keys for the gateway only |

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
Kubernetes: `k8s/external-secret-gateway.yaml` (ESO) makes one secret per service, each with
only what that service uses: `yal-gateway-credentials` (the gateway's token and the vendor keys,
read by the gateway alone), `yal-supervisor-secrets` (database, gateway token, service JWT
secret, `VAULT_KEYS`, `TEMPORAL_CODEC_KEY`; the API, its workers and the rollout check) and
`yal-memory-secrets` (the memory service's database and the service JWT secret). Every service
runs with `ENV=production`: service authentication, the vault keys and the Temporal history key
are then required, and a missing one stops the process at boot.

### Sandbox provider (D10)

| Env | Default (compose) | Notes |
|---|---|---|
| `SANDBOX_PROVIDER` | `local_docker` | Root + staging compose set `SANDBOX_PROVIDER:-local_docker` on supervisor. githi compositions pin the `githi-docker-v1` sandbox profile. |
| `MULTI_TENANT_STRICT=true` | off | **Required for shared prod:** refuses `local_process` / `local_docker` at boot; use **`e2b`** or `none`. |

**E2B network/egress (multi-tenant path):** E2B sandboxes run on E2B cloud VMs with SaaS-managed egress. Supervisor never injects vendor API keys or `GATEWAY_TOKEN` into sandbox env — only optional `vk_*` for agent→gateway. Operators must configure E2B account/firewall policy to allow DNS + model-gateway egress only.

**Docker hardening:** `local_docker` uses `network_mode=none`, `cap_drop=ALL`, mem/CPU limits. For controlled egress in K8s, apply `k8s/networkpolicy-sandbox-default-deny.yaml` in the sandbox namespace.

### Phase 2 multi-tenant guardrails (optional env)

| Variable | Purpose |
|---|---|
| `MULTI_TENANT_STRICT=true` | Refuse `local_docker` / `local_process` at boot |
| `TENANT_MAX_SESSIONS` | Per-tenant active session cap |
| `TENANT_MAX_SANDBOXES` | Per-tenant live sandbox cap |
| `GLOBAL_MAX_SESSIONS` | Fleet-wide active session cap |
| `TENANT_MAX_COST_PER_TENANT` | Most a tenant may spend on models in a calendar month (UTC), across all its sessions. Counted as spent, so erasing sessions does not give it back; at the limit, runs stop before their next model call and new sessions are refused until the month turns |
| `TENANT_RATE_LIMIT_PER_MINUTE` | Per-tenant HTTP rate limit |
| `WORKSPACE_MAX_SCOPE_BYTES` | Most one workspace stores, every version of its files counted once per distinct content (default 10 GiB; `0`: no limit). Past it, uploads are refused with 413 and an agent's changed files are reported not saved |
| `TEMPORAL_CODEC_KEY` | Seals what Temporal stores of each session (32 bytes, base64 or hex; same on every API replica and worker). Required with `ENV=production` or `staging` unless `TEMPORAL_PLAINTEXT_HISTORY=1` says the server encrypts at rest; `TEMPORAL_CODEC_OLD_KEYS` alone is the first step of turning it on |
| `SESSION_RETENTION_DAYS` | Erase ended sessions after this many quiet days (an hourly sweep on the API; `SESSION_RETENTION_SWEEP_S` to change). Unset: kept |
| `PLUG_ALLOWED_HOSTS` | More hosts a published service URL (gateway, MCP, ACP, recall server) may name, comma-separated; `*` trusts any. Workers call these URLs, so the default is only the platform's own services |
| `SANDBOX_DOCKER_DISK_LIMIT` | A `local_docker` sandbox's disk cap (e.g. `10G`); needs overlay2 on XFS with project quotas. Processes (512), memory, CPU, network and privileges are always limited |

Metrics snapshot: `GET /metrics` on each supervisor replica (a verified caller; its own tenant's counters).

### Session workers

The supervisor image has two roles: `args: ["api"]` (supervisor-deployment.yaml) and
`args: ["worker"]` (worker-deployment.yaml). Workers carry `WORKER_MAX_CONCURRENT_RUNS` runs
each and answer `/healthz` and `/readyz` on `WORKER_HEALTH_PORT` (8081). Keep at least one
worker running; the PodDisruptionBudget does this during drains. With none running, sessions
wait in Temporal and nothing is lost.

- Fleet health: `GET /v1/ops/workers` on the API (JSON), and `GET /v1/ops/workers/metrics`
  (Prometheus) for `alerts/workers.rules.yml`.
- Autoscaling on queue backlog and busy slots rather than CPU: `k8s/worker-autoscaling.yaml`
  (KEDA). A worker waiting on a model uses little CPU even when its slots are full.

### Alerts

Scrape these, then load `alerts/*.rules.yml` into Prometheus (CI checks the rules with
`promtool check rules` and runs `promtool test rules alerts/platform.rules.test.yml`):

| Scrape | Serves | Rules |
|---|---|---|
| each API replica, `GET /v1/ops/workers/metrics` | the worker fleet | `workers.rules.yml` |
| each API replica, `GET /v1/ops/metrics` | `yal_api_responses_total{code}`; `yal_dependency_up{dependency}` for the database, Temporal, the sandbox provider, the model gateway and the memory service (probed on each scrape, 3 s each) | `platform.rules.yml` |
| each gateway replica, `GET /v1/metrics` | `yal_gateway_responses_total{code}` (model calls only) and `yal_gateway_spend_usd_total` | `platform.rules.yml` |

They page when the database, Temporal, the gateway or the sandbox provider stops answering, and
warn on over 5% of API responses failing, over 20% of model calls failing, the memory service
down, and model spend over $25 in an hour (set that threshold to your own usage). All three
endpoints carry counts only (no tenant, session or key), like the fleet's. Counters are per
process, so a restart starts them at zero; `rate()` and `increase()` allow for that.

### Backups and restore

What holds state, and how to keep it:

| Store | What it holds | How to back it up |
|---|---|---|
| Postgres `supervisor` (`DATABASE_URL`) | every session's journal (the source of truth), runs, traces, artifacts' and workspace files' metadata, scopes and grants, sealed secrets, the catalog | Continuous archiving (WAL + base backups, e.g. pgBackRest or your provider's point-in-time recovery). |
| Postgres `memory` (`MEMORY_DATABASE_URL`) | long-term memory: notebooks, facts, their history | The same, with the same schedule. |
| The product's Postgres (backend `DATABASE_URL`) | accounts, harnesses, tools, environments, session links, datasets | The same. |
| Postgres `temporal`, `temporal_visibility` | running workflows and their histories | The same; see below for what losing it costs. |
| Object storage (`OBJECT_STORAGE`, `OBJECT_STORAGE_BUCKET`) | the bytes of artifacts, workspace files (each version) and checkpoints | Bucket versioning plus replication to a second bucket or region. |
| `BLOB_DIR` | journal payloads too large to keep in a row, and lost sandboxes' logs | A persistent volume every API replica and worker mounts; snapshot it. |
| `VAULT_KEYS` | the keys that seal secrets in the database | Keep in your secret manager, outside the database's backups. Without them the sealed secrets cannot be read. |

The database holds the references and the stores hold the bytes, and bytes are only ever added
under new keys. So take each database's backup **before** the object-storage and `BLOB_DIR`
snapshots of the same moment: every reference the restored database holds then finds its bytes.

**Restoring.** Stop the workers and API replicas. Restore the databases to the same point in
time. Restore the bucket and `BLOB_DIR` to that point or later, then start the API and the
workers. If Temporal's databases are restored to an earlier point than the supervisor's, or
lost, sessions whose workflows no longer exist cannot take new messages. Their conversations
are still in the journal and can be read, and forking one starts a new session that carries the
conversation on.

**Drill it.** Once a quarter, restore into a staging stack and check that sessions list, a
transcript opens, an artifact downloads and a fork can be sent a message.

### Safe deploys

Workers register as versions of the `yal-supervisor` worker deployment, with the image tag as
the build id (`WORKER_DEPLOYMENT_NAME`, `WORKER_BUILD_ID`). Sessions auto-upgrade to each new
build, so a rolling update of `worker-deployment.yaml` (with `WORKER_ROLLOUT=auto`) is safe.

1. Before rolling out a new image, run `k8s/rollout-check-job.yaml` with that image. It
   replays running sessions against the new code and fails if any would break.
2. Roll out the image.
3. Check with `python -m supervisor rollout status`, or `GET /v1/ops/rollout` on the API.

For blue/green, run one Deployment per build with `WORKER_ROLLOUT=manual`, then use
`rollout ramp BUILD 10` and `rollout promote BUILD`. The root README's "Safe deploys" has each
step's time limit, retries and worker-death behaviour.

### Metrics

```bash
python services/supervisor/scripts/measure_sandbox_metrics.py --image <digest-pinned-harness>
```

Paste output into `services/supervisor/ARCHITECTURE.md` Step 9 table.
