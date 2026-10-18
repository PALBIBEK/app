# Track B Status — Workspace + Sandbox Spine

**Started:** 2026-08-29  
**Last Updated:** 2026-08-29 (WP-M7 VibeSec pass)  
**Patterns:** `PYTHON_PATTERNS_MASTERCLASS.md` (Protocol, DTO, Factory, Service Layer, fakes)

## Checklist

- [x] **WP-M0** Freeze dual-SoT contracts + ObjectStorage/WorkspaceStore Protocols + recovery matrix + sandbox hydrate/sync hooks (default unsupported)
- [x] **WP-M1** S3-compatible ObjectStorage adapter + factory + SeaweedFS in `docker-compose.yml` (unit tests use fake client)
- [x] **WP-M2** scopes + workspace_files models + Alembic `0007_workspace` (HTTP APIs deferred)
- [x] **WP-M3** Hydrate/sync `/workspace` on SandboxProvider (`local_process` + WorkspaceHydrator; memory fakes)
- [x] **WP-M4** Authz principal → scope → grants (`AuthzService` + `GrantStore`; `require_grant`; schema)
- [x] **WP-M5** Wire githi tools via sandbox + gateway virtual keys (`requires_sandbox: true`)
- [x] **WP-M6** Local acceptance + chaos
- [x] **WP-M7** VibeSec pass

## Post–Track B stages

- [x] **Stage 1** Postgres `WorkspaceStore` + `GrantStore` + bootstrap env wiring
- [x] **Stage 2** Workspace HTTP CRUD + `ARTIFACT_DOWNLOAD` presign
- [x] **Stage 3** D1 AI-plane service auth (JWT claims + gateway fail-closed)
- [ ] **Stage 4** R1 outbox recovery + R4/R5 orphan paths
- [x] **Stage 5a** Docker TreeIO hydrate/sync + env scrub (E2B + Seaweed live chaos still open)

## Stage 5a design lock

- `DockerWorkspaceTreeIO` + `as_tree_io` / hydrate/sync on `LocalDockerSandbox`
- Factory forwards hydrator; `sanitize_sandbox_env` on docker create/spawn
- Fake-client unit: hydrate → mutate → sync roundtrip

## Stage 3 design lock

- Supervisor: service JWT → tenant/principal from claims; spoofed `X-*` → 403 (existing + workspace HTTP)
- Gateway: `AUTH_REQUIRED` / production|staging without `MODEL_GATEWAY_TOKEN` → 503; constant-time compare
- **Residual:** system BFF outbound mint client + memory-service auth (prime_agent / memory, not Track B spine)

## Stage 2 design lock

- `GET/PUT/DELETE /v1/scopes/{id}/files[/{path}]` against durable store (not sandbox FS)
- `POST .../download` requires `artifact:download`; returns object-store presign
- Grant miss → **404** (no enumeration)
- `SupervisorState.workspace_store` wired from bootstrap

## Stage 1 design lock

- `WORKSPACE_STORE=postgres` / `GRANT_STORE=postgres` require `session_factory`
- Bootstrap honors `WORKSPACE_STORE`, `GRANT_STORE`, `OBJECT_STORAGE` (no hardcoded memory)
- Soft-delete + CAS + checksum hydrate/sync parity with memory store
- Partial unique live paths: Alembic `0009_workspace_partial_unique`

## Test evidence (Stage 1)

```text
uv run --extra dev pytest -q tests/unit/test_postgres_workspace_grant_stores.py \
  tests/unit/test_workspace_schema.py tests/unit/test_wp_m5_session_workspace.py \
  tests/unit/test_wp_m6_*.py tests/unit/test_wp_m7_*.py
# → 56 passed (subset gate)
```

## WP-M7 design lock

- **P0:** scope attach no longer re-bootstraps owner (IDOR); `LocalProcessSandbox` FS via TreeIO; scope ⇒ authz required
- **P1:** tool arg strip expanded; env deny vendors; VK empty allowlist deny + ttl/budget caps; create maps grant deny → 403; `invoke_tool` respects ready
- **Residual:** header/static-bearer principal stub; VK spend metering; Postgres adapters; workspace HTTP/presign

## Test evidence

```text
uv run --extra dev pytest -q \
  tests/unit/test_wp_m7_*.py \
  tests/integration/test_wp_m7_*.py \
  tests/unit/test_wp_m6_*.py \
  tests/integration/test_wp_m6_local_acceptance.py \
  tests/unit/test_wp_m5_session_workspace.py \
  tests/unit/test_workspace_authz.py \
  tests/unit/test_sandbox_env.py \
  tests/unit/test_githi_sandbox_tools.py
# → 70 passed

# model-gateway
pytest -q tests/test_virtual_keys.py tests/test_gateway.py
# → 14 passed
```

## Key paths (M7)

- `session/manager.py` (`_ensure_scope_binding`, `invoke_tool` ready gate)
- `seams/session_workspace.py` (`require_sandbox_exec` fail-closed)
- `sandbox/local_process.py`, `sandbox/workspace_tree.py`, `sandbox/env.py`
- `agent/runtimes/githi/tools/provider.py`
- `api/sessions.py`, `api/tools.py`
- `model_gateway/virtual_keys.py`, `model_gateway/app.py`
- `tests/unit/test_wp_m7_*.py`, `tests/integration/test_wp_m7_session_scope_idor.py`
