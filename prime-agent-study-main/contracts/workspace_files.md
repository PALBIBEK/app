# Workspace files schema sketch (Track B / WP-M2 will migrate)

**Status:** Frozen sketch for WP-M0/M1 — **no Alembic yet**  
**Date:** 2026-08-29

## `scopes`

| Column | Type | Notes |
|---|---|---|
| id | text pk | |
| type | text | `personal` / `project` / `channel` |
| owner_principal_id | text | |
| name | text | |
| created_at | timestamptz | |

## `workspace_files`

| Column | Type | Notes |
|---|---|---|
| id | text pk | |
| scope_id | text fk → scopes | |
| path | text | relative posix path |
| content_sha256 | text | |
| object_key | text | `blobs/{aa}/{sha256}` |
| size_bytes | bigint | |
| mime_type | text null | |
| version | int | monotonic per path |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz null | soft delete |

**Unique:** `(scope_id, path)` where `deleted_at IS NULL` (partial unique index).

## Runtime DTO

See `supervisor.seams.workspace_store.WorkspaceFileRecord`.
