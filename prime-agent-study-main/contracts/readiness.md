# Readiness probes vs product status (D2)

## Split (normative)

| Surface | Audience | Checks | Fail impact |
|---------|----------|--------|-------------|
| System BFF `GET /health`, `GET /ready` | K8s / LB | BFF process + **system** Postgres only | Ejects login/CRUD from load balancer |
| `GET /api/v1/platform/status` | Logged-in UI | Aggregates supervisor `/readyz` (auth’d) | Disables Test/Run; mutations → `503 AI_PLATFORM_NOT_READY` |
| Supervisor `GET /healthz` | K8s liveness | Process only | Restart pod |
| Supervisor `GET /readyz` | K8s readiness + product | Drain, DB, catalog, sandbox, **gateway** `/v1/readyz`, **memory** `/v1/readyz` when required | Remove from service; `run_allowed=false` |
| Gateway / memory `GET /v1/healthz` | Liveness | Process only | Restart |
| Gateway / memory `GET /v1/readyz` | Readiness | Credentials/routes or DB ping | Remove from service; supervisor fails ready |

**Browser never probes AI** — only the system BFF calls supervisor with service credentials.

## Safe ready reasons

Enums only (no hosts, tokens, SQL): `not_configured`, `draining`, `database`, `sandbox`,
`catalog`, `gateway`, `memory`, `credentials`, `routes`, `provider`, `auth`, `timeout`,
`unreachable`, `upstream_error`, `not_ready`.

## Memory policy

When `MEMORY_SERVICE_URL` is set or `MEMORY_READY_REQUIRED=true`, durable memory is a
**hard** Run dependency (supervisor `/readyz` → `reason=memory` on failure).

**Residual (soft-skip):** if `MEMORY_SERVICE_URL` is unset and `MEMORY_READY_REQUIRED` is
false, Run may proceed without durable memory. Document that product choice explicitly
in the deploy profile — staging sets both URL and `MEMORY_READY_REQUIRED=true`.
