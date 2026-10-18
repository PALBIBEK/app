# Services Architecture

The `services/` folder contains **Protocol definitions** and **service abstractions** that sit above the raw providers. These handle authorization, workspace lifecycle, and cross-cutting concerns.

---

## File Overview

| File | Primary Object | Purpose |
|------|----------------|---------|
| **workspace_store.py** | `WorkspaceStore` Protocol | File metadata store (Postgres) + blob orchestration. CAS versioning for conflict detection. |
| **object_storage.py** | `ObjectStorage` Protocol | Raw blob storage (S3/SeaweedFS). put/get/delete/presigned URLs. |
| **authz.py** | `GrantStore` + `AuthzService` Protocols | Authorization: Principal → Scope → Grants. 5 grants: workspace:read/write, sandbox:exec, artifact:download, scope:admin |
| **authz_service.py** | `StoreAuthzService` | Implementation of AuthzService. Fail-closed grant checks. |
| **virtual_keys.py** | `VirtualKeyClient` Protocol | Mint/revoke session-scoped API keys with budget + TTL |
| **workspace_hydrator.py** | `WorkspaceHydrator` Protocol | Durable ↔ sandbox cache sync. Per-scope mutex for safety. |
| **memory_service_client.py** | HTTP helpers | Talk to Memory Service for semantic recall. write_memory_item() + recall_memory_items() |
| **authorized_workspace.py** | Helper functions | Gates hydrate/sync behind authz checks. `authorized_hydrate()`, `authorized_sync()`, `authorized_exec()` |
| **session_gateway.py** | `attach_minted_gateway()` | Wire HttpModelGatewayClient with session's virtual key |
| **session_workspace.py** | `SessionWorkspaceService` | Session lifecycle: hydrate → dirty tracking → sync. Conflict recovery. |

---

## How They Connect

```
┌─────────────────────────────────────────────────────────────────┐
│                    SessionWorkspaceService                       │
│         (orchestrates session lifecycle)                         │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌──────────┐ ┌─────────────────┐
│ AuthzService │ │ Hydrator │ │ VirtualKeyClient│
│  (grants)    │ │  (sync)  │ │   (API keys)    │
└──────┬───────┘ └────┬─────┘ └────────┬────────┘
       │              │                │
       ▼              ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  GrantStore  │ │WorkspaceStore│ │   Gateway    │
│  (postgres)  │ │  (postgres)  │ │   (HTTP)     │
└──────────────┘ └──────┬───────┘ └──────────────┘
                        │
                        ▼
                ┌──────────────┐
                │ObjectStorage │
                │  (S3 blobs)  │
                └──────────────┘
```

---

## Key Patterns

1. **Protocol + Implementation split**: `authz.py` defines `GrantStore`, `authz_service.py` implements it
2. **Authorized wrappers**: Never call hydrator directly - use `authorized_hydrate()` which checks grants first
3. **Fail-closed**: `StoreAuthzService.require()` throws if grant missing, never silently allows
4. **Session state tracking**: `SessionWorkspaceState` tracks dirty/ready/conflicts for the session

---

## Principal → Scope → Grant Model

### Concepts

| Concept | What It Is | Example |
|---------|------------|---------|
| **Principal** | Who is asking (identity) | `user:alice@acme.com` |
| **Scope** | What resource they want to access | `workspace:proj-123` |
| **Grant** | What action they can perform | `workspace:write` |

### The 5 MVP Grants

```python
class Grant(StrEnum):
    WORKSPACE_READ = "workspace:read"      # Read files from workspace
    WORKSPACE_WRITE = "workspace:write"    # Write/modify files
    SANDBOX_EXEC = "sandbox:exec"          # Execute code in sandbox
    ARTIFACT_DOWNLOAD = "artifact:download" # Download build artifacts
    SCOPE_ADMIN = "scope:admin"            # Manage scope membership
```

### Example Scenario

```
Company: Acme Corp (tenant_id: "acme")
Project: ML Pipeline (scope_id: "proj-ml-pipeline")

Team Members:
├── Alice (principal: "user:alice")  → Owner
├── Bob (principal: "user:bob")      → Developer
└── Carol (principal: "user:carol")  → Viewer
```

**Grant Table (in GrantStore):**

| Principal | Scope | Grants |
|-----------|-------|--------|
| `user:alice` | `proj-ml-pipeline` | `workspace:read`, `workspace:write`, `sandbox:exec`, `scope:admin` |
| `user:bob` | `proj-ml-pipeline` | `workspace:read`, `workspace:write`, `sandbox:exec` |
| `user:carol` | `proj-ml-pipeline` | `workspace:read` |

### What Each Person Can Do

```
Alice (Owner):
✓ Read files          (workspace:read)
✓ Write files         (workspace:write)
✓ Run code            (sandbox:exec)
✓ Add/remove members  (scope:admin)

Bob (Developer):
✓ Read files          (workspace:read)
✓ Write files         (workspace:write)
✓ Run code            (sandbox:exec)
✗ Add/remove members  (no scope:admin)

Carol (Viewer):
✓ Read files          (workspace:read)
✗ Write files         (no workspace:write)
✗ Run code            (no sandbox:exec)
✗ Add/remove members  (no scope:admin)
```

### Code Flow Example

```python
# Carol tries to write a file
principal = Principal(id="user:carol", tenant_id="acme")
scope_id = "proj-ml-pipeline"

# AuthzService checks:
await authz.require(principal, scope_id, Grant.WORKSPACE_WRITE)
# ↓
# 1. Is carol a member of proj-ml-pipeline? YES
# 2. Does carol have workspace:write grant? NO
# 3. Raise GrantDenied!

# Result: Carol cannot write files
```

### Why This Matters for the Platform

1. **Multi-tenant isolation**: Each tenant's scopes are separate
2. **Fine-grained access**: Different team members have different capabilities
3. **Fail-closed security**: No grant = no access (never assume allowed)
4. **Composable with harnesses**: When a harness spawns a subagent, the subagent inherits the session's principal - it can't escalate privileges

---

## Session Lifecycle with Authz

```
1. Session Start
   └── Principal extracted from auth token

2. Workspace Bind (scope_id assigned)
   └── Check: is_member(principal, scope_id)?

3. Hydrate (load files to sandbox)
   └── Check: has_grant(principal, scope_id, WORKSPACE_READ)?

4. Tool Execution (agent runs code)
   └── Check: has_grant(principal, scope_id, SANDBOX_EXEC)?

5. Sync (save changes back)
   └── Check: has_grant(principal, scope_id, WORKSPACE_WRITE)?
```

Each step is gated. If any check fails, the operation is denied with a typed error.
