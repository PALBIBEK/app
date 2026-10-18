# Workspace Authz Grants (Track B / WP-M0)

**Status:** Frozen grant vocabulary for control-plane checks  
**Date:** 2026-08-29

## Model

```text
Principal (from X-Tenant-Id / future Bearer)
  → Scope membership (scope_id)
    → Grants
```

Sandbox/agent processes are **untrusted** for authz. Path confinement inside
`/workspace` is defense-in-depth only.

## Grant enum (MVP)

| Grant | Allows |
|---|---|
| `workspace:read` | list/stat/hydrate/download |
| `workspace:write` | commit_blob/sync/soft_delete |
| `sandbox:exec` | create/exec in sandbox for scope |
| `artifact:download` | presigned download URL |
| `scope:admin` | manage memberships / resolve conflicts |

## FastAPI shape (normative, implement WP-M4)

```python
async def require_grant(scope_id: str, grant: str) -> Principal: ...
```

Call **before** hydrate/sync/exec/presign. Object keys must not be guessable;
presign only after grant check. Model provider secrets never enter sandbox env
(virtual keys stay in model-gateway — WP-M5).

## Out of scope WP-M0

Persisted membership tables and HTTP middleware — vocabulary + tests of
policy helpers only if present; full wiring is WP-M4.
