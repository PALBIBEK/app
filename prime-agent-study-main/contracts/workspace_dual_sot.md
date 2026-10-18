# Workspace Dual Source of Truth (Track B / WP-M0)

**Status:** Frozen for Track B  
**Date:** 2026-08-29  
**Patterns:** `typing.Protocol` seams (PYTHON_PATTERNS_MASTERCLASS §2/§5), claim-check for bytes

## Principle

```text
Journal (Postgres events)     = SoT for turns / tools / run lifecycle
Postgres workspace_files      = SoT for path → blob pointers + version
Object store (S3/Seaweed/GCS) = SoT for immutable file bytes
Sandbox /workspace            = hot cache only (not durable)
```

Do **not** event-source file bodies into the session journal. Journal may record
`file_id`, `path`, `content_sha256`, `version` only (claim-check).

## Ownership matrix

| Concern | Owner | Not owner |
|---|---|---|
| ToolStart / ToolEnd / RunEnd | Journal | Object store |
| Path namespace `(scope_id, path)` | Postgres | Sandbox FS |
| Blob bytes | Object store | Journal |
| Agent POSIX view | Sandbox `/workspace` | Control plane (except hydrate/sync) |
| Authz decisions | Control plane grants | Sandbox process |

## Addressing

- **Logical key (Postgres):** unique `(scope_id, path)` among live (`deleted_at IS NULL`) rows.
- **Object key:** content-addressed `blobs/{sha256[0:2]}/{sha256}`.
- Row fields (normative sketch for WP-M2):  
  `id`, `scope_id`, `path`, `content_sha256`, `object_key`, `size_bytes`,
  `mime_type`, `version`, `created_at`, `updated_at`, `deleted_at`.

Overwrite path → new blob + `version += 1`. Old blobs GC when refcount hits 0 (WP-M2+).

## Soft delete

Set `deleted_at`. Soft-deleted paths are invisible to hydrate/list. Blob GC is async
after retention (not WP-M0).

## Versioning / CAS

Monotonic `version` per path. Sync uses compare-and-swap:
`UPDATE … WHERE version = expected`. Conflict → fail closed (see recovery matrix).

## Conflict rules (sandbox vs durable)

| Situation | Winner |
|---|---|
| Durable version > sandbox stamp | Durable → re-hydrate path |
| Sandbox dirty, durable unchanged | Sandbox → sync (put + bump) |
| Both changed (split brain) | **Fail closed**; mark conflict; default = durable wins + journal metadata event |
| Two sandboxes writing one scope | **Forbidden** in Track B MVP (scope lease → one sandbox) |

## Hydrate / sync triggers

**Hydrate** before first file-needing tool after create/reattach; after sandbox kill;
never as SoT for UI downloads (prefer durable).

**Sync** after mutating tools (at least end-of-turn); before destroy/pause when
artifacts must survive. Do not treat sandbox pause/snapshot as user-data backup.

## Seam placement

| Contract | Module |
|---|---|
| `ObjectStorage` Protocol | `supervisor.seams.object_storage` |
| `WorkspaceStore` Protocol | `supervisor.seams.workspace_store` |
| Sandbox hydrate/sync hooks | `supervisor.sandbox.base` (default: unsupported) |
| Normative prose | this file + `workspace_recovery_matrix.md` + `workspace_authz_grants.md` |

## Out of scope for WP-M0

SeaweedFS adapter, GCS adapter, Alembic tables, HTTP workspace API, Docker hydrate,
FUSE, Temporal workers, flipping `requires_sandbox: true` on githi.
