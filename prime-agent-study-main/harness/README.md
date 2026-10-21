# YAL agent catalog

The versioned, digest-pinned artifacts a supervisor session is composed from.
Sessions run on githi, the supervisor's own agent runtime (or `echo` for
lightweight development sessions); a session pins exact digests of everything
below at create, and runs on them for its whole life.

## Layout

| Path | Role |
|---|---|
| `versions/*.yaml` | Harness manifests: runtime, recovery classes, budget, guards, approvals, sandbox mode |
| `compositions/` | AgentComposition entries: a manifest plus a prompt pack, tool set, sandbox and memory profile |
| `prompt_packs/` | PromptPack artifacts |
| `tool_sets/` | ToolSet artifacts (the tools the model is offered) |
| `sandbox_profiles/` | SandboxProfile artifacts (provider, image, file effects) |
| `memory_profiles/` | MemoryProfile artifacts (compaction and recall) |

## Two tool definitions

| Source | Purpose |
|---|---|
| `versions/*.yaml` → `tools:` | **Recovery classification only** (`pure`, `idempotent`, …) |
| `tool_sets/*.json` | **The tools offered to the model**, bound by the composition |

Recovery tool names must be a subset of the composition's ToolSet; CI checks this
(`verify_recovery_tools_in_compositions()`).

## Publishing catalog changes

After editing `tool_sets/`, `prompt_packs/`, `sandbox_profiles/`, `memory_profiles/`
or `compositions/`:

```bash
python3 scripts/recompute-digests.py
```

Commit the updated `digest` fields; the supervisor's
`verify_composition_catalog()` must pass at boot.

### Changing a shipped artifact keeps the old version

Sessions and tenant compositions pin exact digests, so a version must stay
resolvable after a release replaces it:

- Each boot records every shipped artifact in the database (insert-only), so a
  version shipped once stays available from there in later releases.
- Before editing an artifact that may never have been recorded (anything a
  deployment ran before this rule), copy its current file to `previous/<kind>/`
  (e.g. `previous/tool_sets/githi-tools-1.0.0.json`) and bump `semver` in the
  edited file. `previous/` is loaded before the current files, never rewritten by
  `recompute-digests.py`, and its digests are verified like any other.

## Shipped manifests

| Version | Runtime | Sandbox | Mode |
|---|---|---|---|
| `githi-v1` | `githi` | required (built on first use) | sticky |
| `kg-v1` | `echo` (dev / lightweight) | none | stateless |

## Contracts

- Supervisor [`../services/supervisor/PROGRESS.md`](../services/supervisor/PROGRESS.md)
