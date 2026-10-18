# Next Phase: Githi Integration + Workspace/Sandbox

**Status:** Research complete — implementation not started  
**Date:** 2026-08-29  
**Updated after:** nested-tree codebase audit (Phase 1 still open)

---

## Verdict

**Two tracks, sequenced:**

1. **First — Phase 1.5 exit (blocking):** githi lives under the **nested** tree but is not integrated (`factory` only knows `echo`/`harness`; `send_message` still placeholders; OpenRouter/tools/tests incomplete).
2. **Then — Workspace/Sandbox spine (YC QM plan):** ObjectStorage + scopes + hydrate/sync + control-plane authz.

Canonical root:

```text
prime-agent-study-main/prime-agent-study-main/   ← real supervisor/memory/gateway/githi
prime-agent-study-main/{services,contracts}/     ← outer stubs (mostly empty)
```

Do **not** flip `requires_sandbox: true` until:

`POST /v1/sessions` with `runtime=githi` → SSE `tool_start` / `tool_end` / `message_end` / `settled`.

After that:

```text
Scope → ObjectStorage + Postgres metadata → hydrate /workspace
  → LocalDocker sandbox → githi tools via sandbox
  → sync dirty files → journal intent/completion → SSE resume
```

Keep **session journal**. Do **not** event-source workspace file bytes.

---

## Research inputs

| Agent | Finding |
|-------|---------|
| Githi codebase explore | Nested runtime modules exist; **unwired** loop, factory, gateway, tools, tests. Outer stubs empty. Next = Phase 1.5 exit |
| YC plan explore | 21-phase Workspace≠Sandbox blueprint; QM/Daytona/Vercel patterns |
| QM↔githi map | Supervisor has SandboxProvider + journal; **GAP** = ObjectStorage, scopes, hydrate/sync, real authz |
| Production arch | WAL effect-kinds, Docker+egress allowlist, Gateway virtual keys, Tier1 memory sync, SSE `Last-Event-ID` |

Primary plan: `agent-platform-research-insight/agent_platform_phase_by_phase_implementation_planWORKSPACESANDBOX..md`  
Upstream: https://github.com/yc-software/qm

---

## Evidence (nested tree)

| Check | Result |
|-------|--------|
| `factory.known_runtime_names()` | `{"echo", "harness"}` — **no githi** |
| `GithiRuntime.send_message` | `# TODO: Phase 1 implementation` → yields placeholder `settled` only |
| Runtime modules | `loop.py`, `state.py`, tools/file/{read,write,ls} present |
| OpenRouter provider | File exists; not registered in gateway factory |
| Outer `services/` / `contracts/` | Empty / stub vs nested real platform |

---

## Gap summary

### Phase 1.5 blockers (do first)

| Item | Gap |
|------|-----|
| Factory registration | Register `githi` |
| Loop wiring | Call `AgentLoop`; emit real SSE events |
| Gateway OpenRouter | Register provider + `githi` route; keys only in gateway |
| Tools | Finish edit/grep/bash; enable builtin register; path confinement **before** bash |
| Harness tool_sets | Populate `githi-v1` JSON definitions |
| Tests / E2E | Unit + sessions API acceptance |
| Compose | Memory / routes / env for gateway key |

### Workspace spine (after Phase 1.5 green)

| Capability | Gap |
|------------|-----|
| Sandbox | Wire tools through provider; then flip `requires_sandbox` |
| Journal | effect_kind + incomplete-intent recovery |
| ObjectStorage | Interface + SeaweedFS (dev) / GCS (later) |
| Scopes / workspace_files | Schema + APIs; opaque object keys |
| Hydrate / sync | On run start / settle |
| Authz | Principal→scope→grants; never trust model `scope_id` |
| Gateway vault | Virtual keys per session; revoke on kill |

---

## Work packages (ordered — implement only after approval)

### Track A — Phase 1.5 exit (blocking)

#### WP-A1 — Wire the brain
- [ ] `GithiRuntime.send_message` → `AgentLoop` (replace Phase-1 placeholder)
- [ ] Emit `turn_start` / `tool_start` / `tool_end` / `message_end` / `settled`
- [ ] Persist in-session message history

#### WP-A2 — Factory + session registration
- [ ] Add `"githi"` to `known_runtime_names` / `create_runtime`
- [ ] Pin harness `githi-v1`; smoke via sessions API

#### WP-A3 — Gateway seam
- [ ] Register OpenRouter (or HTTP client to gateway) in gateway factory
- [ ] Add `githi` model route; map async stream ↔ supervisor client
- [ ] Keep provider keys only in gateway settings

#### WP-A4 — Core tools + path safety
- [ ] Enable builtin tool registration; finish edit / grep / bash (host, timeouts)
- [ ] Path confinement under workspace root **before** enabling bash
- [ ] Populate `harness/tool_sets/githi-v1/*.json`

#### WP-A5 — Tests + Compose acceptance
- [ ] Unit tests: runtime events, tools, gateway provider
- [ ] Acceptance: `POST /v1/sessions` `runtime=githi` → SSE tool events
- [ ] Compose: memory if required; document ports; `AVAILABLE_RUNTIMES`

**Phase 1.5 exit gate:** one coding turn with ls/read (preferably write) over SSE.

---

### Track B — Workspace / Sandbox spine (after A green)

#### WP-M0 — Freeze contracts
- [ ] Dual SoT in contracts (journal vs workspace)
- [ ] Single `Sandbox` provider API (supervisor wins)
- [ ] Recovery matrix: ReadOnly / Idempotent / Compensatable / Irreversible

#### WP-M1 — ObjectStorage + local Compose
- [ ] `ObjectStorage` interface (put/get/delete/exists/signed URLs)
- [ ] SeaweedFS (or S3-compat) in docker-compose
- [ ] No vendor types in app code

#### WP-M2 — Scope + workspace metadata
- [ ] Tables: users/tenant map, scopes, workspace_files, agent_runs, artifacts
- [ ] Keys: `workspaces/{scope_id}/objects/{random_id}`
- [ ] APIs: create scope, list/get files

#### WP-M3 — Hydrate / sync
- [ ] Start: hydrate → sandbox `/workspace`
- [ ] Settle: sync dirty files → object store + metadata
- [ ] Prompt: durable under `/workspace` only; no FUSE

#### WP-M4 — Control-plane authz
- [ ] Principal → scope membership before bind/hydrate
- [ ] Reject model-supplied `scope_id` as authority
- [ ] Short-lived capabilities; no broad cloud creds in sandbox

#### WP-M5 — Wire githi through sandbox
- [ ] File/bash only via sandbox; journal `tool_start` **before** exec
- [ ] Flip `requires_sandbox: true` + pin image digest
- [ ] LLM via Gateway; session virtual key; kill = stop + revoke + destroy

#### WP-M6 — Local acceptance
- [ ] User → scope → sandbox → `/workspace/reports/report.md` survives API kill + sandbox restart
- [ ] Chaos mid-tool; 10 concurrent runs

#### WP-M7 — VibeSec pass
- [ ] Secrets out of sandbox/logs; auth on routes; path/cross-scope tests; egress allowlist

### Defer until WP-M6 green
GCS/GcpSandbox, MicroVM/E2B default, semantic LLM cache, FUSE, Temporal, workspace vector search, multi-region, outer `backend/prime_agent` product track.

---

## Success metrics

**Phase 1.5:** `runtime=githi` session produces real tool SSE events end-to-end.

**Workspace spine:** durable workspace, Docker isolation, journal recovery, control-plane authz, &lt;200ms added hop latency locally.

---

## Implementation rule

**No implementation starts until explicitly approved.**  
First code PR: **WP-A1 + WP-A2** only. Track B only after Phase 1.5 exit gate.
