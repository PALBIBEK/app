# Implementation Log — githi → services migration

**Note (2026-08-29):** The long-form historical log (entries 1–17 covering Track A
WP-A1–A5 + Track B WP-M0–M2) was accidentally deleted during WP-M3 doc updates.
Track A status remains in `PHASE1_STATUS.md`. Track B WP-M0–M2 decisions are
summarized below; WP-M3 is fully recorded.

---

## [2026-08-29] Track B WP-M0/M1/M2 — Dual-SoT freeze + ObjectStorage + schema

### Decision (summary)

1. **WP-M0:** Contracts `workspace_dual_sot.md`, `workspace_recovery_matrix.md`,
   `workspace_authz_grants.md`, `workspace_files.md`; Protocols `ObjectStorage`,
   `WorkspaceStore`, `WorkspaceTreeIO`; memory fakes; sandbox hydrate/sync hooks
   default → `WorkspaceSyncUnsupported`.
2. **WP-M1:** `S3ObjectStorage` + factory; SeaweedFS in compose; optional boto3.
3. **WP-M2:** `ScopeRow` / `WorkspaceFileRow`; Alembic `0007_workspace`; HTTP APIs deferred.

### Evidence (at M2 gate)

- Dual-SoT + S3 fake + schema unit tests → **15 passed**

### Research

- [Production dual-SoT patterns](5b70b125-f1c6-4e23-9a67-31a0f4735ed2)
- [Explore sandbox surface](960af30b-70c1-4ebf-be91-01fb19500893)

---

## [2026-08-29] Track B WP-M3 — hydrate/sync on local_process

### Context

Research-first (same gate as M0–M2):

- [Prod hydrate/sync patterns](e545a60a-8f46-4117-a84a-b6736eeb7bbc) — Modal
  commit/reload semantics; WorkspaceHydrator preferred; R7–R12
- [Explore sandbox workspace seams](6ff594ef-7692-41c9-a91c-150ecc9f5154) —
  factory/bootstrap injection gap; spike only
- [Patterns masterclass for M3](04b80004-e373-4dcb-82ea-0a2cb1741838) —
  Protocol/DTO/Factory/DI; memory fakes for exit gate

### Decision

1. `WorkspaceHydrator` / `StoreWorkspaceHydrator` (per-scope mutex) — store owns
   hydrate/sync policy; sandbox is thin `WorkspaceTreeIO` façade
2. Harden memory store: `tree.clear()`, checksum verify (R11), `HydrateIncomplete` (R8)
3. `LocalFsTreeIO` with `asyncio.to_thread` + `clear()`
4. `LocalProcessProvider` accepts `hydrator` or `workspace_store`
5. `create_sandbox_provider(..., hydrator=)` + bootstrap memory hydrator;
   `resolve_sandbox_create` preserves deploy hydrator
6. Recovery matrix extended R7–R12; unit tests on memory fakes only

### Evidence

```text
uv run --extra dev pytest tests/unit/ -k 'workspace or s3 or object_storage'
→ 26 passed
```

### Out of WP-M3

- Postgres WorkspaceStore
- Docker/E2B TreeIO adapters
- SessionManager hydrate/sync triggers (needs scope binding → M4/M5)
- Flipping githi `requires_sandbox: true`

### References

- `TRACK_B_STATUS.md`
- `tests/unit/test_workspace_hydrate_sync.py`
- `contracts/workspace_recovery_matrix.md`


## [2026-08-29] Track B WP-M4 — principal → scope → grants

### Context

Research briefs locked control-plane authz:

- [Prod authz grants patterns](94ece8ec-9d96-4198-8a2c-ac5e5c5e68c0) — separate AuthzService+GrantStore; Postgres rows MVP; no Zanzibar
- [Explore authz scope surface](61c821c1-79f7-44b0-bc54-ad3112db1e18) — tenant header only; no grants/session.scope_id
- [Patterns for WP-M4 authz](5498cb61-a492-4ef0-a1a5-eedb6d0c85c1) — Protocol/DTO/Factory/Service; gate above hydrator

### Decision

1. Frozen `Grant` enum + `Principal` DTO + typed deny errors
2. `GrantStore` Protocol + `MemoryGrantStore` + `StoreAuthzService`
3. `authorized_hydrate` / `authorized_sync` helpers (authz outside hydrator)
4. FastAPI `require_grant(Grant)` dependency; bootstrap wires memory AuthzService
5. Alembic `0008_workspace_authz`: memberships, grants, `sessions.scope_id`
6. Owner bootstrap grants full MVP enum

### Evidence

- `pytest tests/unit/ -k 'workspace or authz or s3 or object_storage'` → **35 passed**

### Out of WP-M4

- Postgres GrantStore adapter, full workspace HTTP APIs, session create hydrate trigger, githi `requires_sandbox: true` (M5)

### References

- `TRACK_B_STATUS.md`
- `tests/unit/test_workspace_authz.py`
- `contracts/workspace_authz_grants.md`


---

**Total entries (this recovered file):** 3 Track B milestone blocks (+ pointer to Track A in PHASE1_STATUS)  
**Status:** Track B WP-M0–M4 done — next WP-M5 (githi + sandbox + virtual keys)  
**Maintainer:** Update after each phase milestone


## [2026-08-29] Track B WP-M5 — githi sandbox tools + virtual keys

### Context

Research briefs:

- [Explore WP-M5 codebase gaps](34f14f39-fb75-4314-a0b8-a027f373821e) — wiring empty; scope → hydrate → tools → vk order
- [WP-M5 design brief](abcd84c6-6d19-4b5c-bc11-90b9f718e794) — Daytona/QM/Vercel patterns; CAS fail-closed; VibeSec checklist

### Decision

1. Flip `githi-v1` `requires_sandbox: true` + image digest; TrackingProvider wraps `LocalProcessProvider`
2. File tools use `WorkspaceTreeIO` when sandbox-enabled; `workspace_relpath` + host pathlib fallback for tests
3. `SessionWorkspaceService` + SessionManager: optional `scope_id`, bootstrap grants, hydrate, dirty/sync
4. Gateway `VirtualKeyStore` + mint/revoke; chat accepts `vk_` tokens; supervisor `HttpVirtualKeyClient` + `attach_minted_gateway`
5. `sanitize_sandbox_env` on local_process spawn/exec

### Evidence

- Supervisor: githi sandbox tools + WP-M5 session + githi sessions API + sandbox env + authz suites green
- Model-gateway: `tests/test_virtual_keys.py` + `tests/test_gateway.py` → 11 passed

### Out of WP-M5

- Postgres WorkspaceStore/GrantStore/VirtualKeyStore
- Workspace HTTP CRUD / presign UX
- Docker/E2B hydrate TreeIO
- Full chaos matrix (WP-M6) / full VibeSec audit (WP-M7)
- bash/webfetch via sandbox

### References

- `TRACK_B_STATUS.md`
- `contracts/workspace_authz_grants.md`
- `contracts/model_gateway.md`


## [2026-08-29] Track B WP-M6 — local acceptance + chaos

### Context

- [WP-M6 design brief](a03150b0-39b9-44f7-86ec-75d4faa3f0ba) — R2/R3/R6–R12 + R1-light; defer R4/R5/full R1
- Recovery matrix: `contracts/workspace_recovery_matrix.md`

### Decision

1. Acceptance suite A1–A7: scope hydrate, tool write+sync, soft-delete, **file survives restart**, grants, path escape, env sanitize
2. Chaos suite with `CrashyTreeIO` + `before_sync_cas` for R8/R10
3. `SessionWorkspaceService.assert_ready` / `WorkspaceNotReadyError`; `ensure_hydrated` clears ready on failure
4. SessionManager.send refuses tools when scope-bound and not ready
5. Symlink escape covered via `LocalFsTreeIO` resolve + `relative_to`

### Evidence

```text
pytest tests/unit/test_wp_m6_*.py tests/integration/test_wp_m6_local_acceptance.py \
  tests/unit/test_workspace_dual_sot.py tests/unit/test_workspace_hydrate_sync.py \
  tests/unit/test_wp_m5_session_workspace.py -q
→ 42 passed
```

### Out of WP-M6

- Full R1 process-kill + outbox close-tool; R4 HTTP list; R5 orphan GC
- Seaweed/GCP chaos; Postgres adapters; full WP-M7 VibeSec

### References

- `TRACK_B_STATUS.md`
- `contracts/workspace_recovery_matrix.md`


## [2026-08-29] Track B WP-M7 — VibeSec pass

### Context

Research briefs:

- [Map WP-M7 security gaps](639657ff-efc0-4179-9798-ca22d7148003) — scope hijack via bootstrap; ungated `read_file`; `invoke_tool` skips ready
- [WP-M7 VibeSec design brief](a2f80431-7f74-4396-adf2-e40a12fb0b75) — S1–S10 checklist; P0–P2 fix order

### Decision

1. **P0-1** `_ensure_scope_binding` bootstraps owner only when creating a new `ScopeRow`; existing scope → `require(SANDBOX_EXEC)` only
2. **P0-2** `LocalProcessSandbox` read/write/exists route through `LocalFsTreeIO`; absolute TreeIO paths rejected
3. **P0-3** `require_sandbox_exec` fails closed if scope bound without authz/principal
4. **P1** strip control-plane tool args; expand env deny prefixes; VK empty allowlist deny + mint ttl/budget caps; session create maps `GrantDenied`/`NotScopeMember` → 403; `invoke_tool` + HTTP tools map `WorkspaceNotReadyError`
5. **P2-light** `list_paths` does not follow dir symlinks; document header principal residual

### Evidence

```text
supervisor gate (M5–M7 + authz/env/githi tools) → 70 passed
model-gateway virtual_keys + gateway → 14 passed
```

### Out of WP-M7 / residual

- Real IdP / claim-bound principal (header stub when auth off)
- VK `max_budget_usd` spend accounting
- Postgres WorkspaceStore/GrantStore/VK; workspace HTTP CRUD/presign
- Docker/E2B env sanitize parity (local_process is spine)

### References

- `TRACK_B_STATUS.md`
- `contracts/workspace_authz_grants.md`
- `contracts/model_gateway.md`
