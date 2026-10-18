# Workspace Recovery Matrix (Track B / WP-M0)

**Status:** Frozen acceptance cases — WP-M6 exercises R2/R3/R6–R12 + R1-light locally  
**Date:** 2026-08-29

Given-when-then rows. Implementations must preserve these invariants.
Journal never stores file bytes.

| ID | Failure | Detect | Recover | Assert |
|---|---|---|---|---|
| R1 | API / supervisor kill mid-tool | Journal `ToolStart` without matching `ToolEnd`; optional outbox `pending` | On restart: if sandbox alive, sync then close tool; else hydrate fresh sandbox and mark tool incomplete | No durable version bump without successful object `put`+verify |
| R2 | Sandbox kill / gone | `is_alive=false` / `SandboxGone` | Create sandbox → **hydrate from durable** → resume; discard dirty cache | Bytes match last committed `content_sha256` |
| R3 | Object-store blip | `ObjectTransient` | Retry with backoff; sync job stays pending; do not bump `version` until put+head ok | Metadata never points at missing key |
| R4 | Journal ahead of files | ToolEnd claims write; no row / version bump | Treat incomplete: re-sync if sandbox alive else fail tool | UI must not list path without blob |
| R5 | Files ahead of journal | Blob+row exist without tool event (e.g. user upload) | Allowed for presigned uploads; agent path journals claim-check stub on next turn | Orphans GC by age if no grant (later phase) |
| R6 | CAS conflict | `cas_update` fails expected version | Fail closed; surface conflict; durable wins by default | No silent last-write-wins across two writers |
| R7 | Kill-without-sync (dirty cache lost) | Sandbox gone; no successful sync since last mutate | New sandbox → **hydrate from durable**; discard dirty | Bytes = last committed `content_sha256` |
| R8 | Partial hydrate (crash mid clear-then-fill) | Marker / `HydrateIncomplete` | Treat cache untrusted → clear + full hydrate again; refuse tools until success | No tool sees mix of leftover + new durable |
| R9 | Soft-delete / empty-dir leftover | Soft-deleted path or empty dirs after file delete | Hydrate uses `clear()`; soft-deleted invisible; prune empty dirs | Soft-deleted path absent; no phantom dirs |
| R10 | CAS conflict during sync | Same as R6 on sync path | Fail closed; path in `conflicts`; durable wins | No silent LWW |
| R11 | Checksum mismatch on hydrate get | `sha256(blob) != row.content_sha256` | Fail closed; do not write bad bytes | Cache never contains unverified bytes |
| R12 | Path traversal / escape | `..`, absolute escape, symlink past root | `WorkspacePathEscape`; reject | No read/write outside sandbox root |

## Non-goals

- Reconstructing file history from journal events
- Using sandbox snapshot as backup of user artifacts
- Multi-writer scopes without a lease
- FUSE / Daytona-style mounts in WP-M3 (local_process push/pull only)
