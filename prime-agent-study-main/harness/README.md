# YAL agent harness

Versioned agent behavior for the YAL supervisor: manifests, composition catalog,
and Pi TypeScript extensions. The supervisor materializes digest-pinned binds into
`session_dir/.yal/`; extensions read those files — they do not own the tool/prompt catalog.

## Layout

| Path | Role |
|---|---|
| `versions/*.yaml` | Harness manifest — recovery classes, budget, guards, sandbox mode |
| `compositions/` | AgentComposition entries (digest-backed) |
| `prompt_packs/` | PromptPack artifacts |
| `tool_sets/` | ToolSet artifacts (production tools for the model) |
| `src/extensions/` | Pi TS extensions (`registerPrompt`, `registerTools`, gateway, …) |
| `images/Dockerfile` | Digest-pinned runtime image with compiled extensions |

## Two tool definitions (read this)

| Source | Purpose |
|---|---|
| `versions/*.yaml` → `tools:` | **Recovery classification only** (`pure`, `idempotent`, …) |
| `tool_sets/*.json` | **Production ToolSet** → `.yal/tools.json` |

Recovery tool names must be a subset of the composition ToolSet. CI verifies this via
`verify_recovery_tools_in_compositions()`.

## Local development

### 1. Build extensions

```bash
cd harness
npm run build          # tsc → dist/extensions/
npm run typecheck
```

### 2. Materialize `.yal/` without a running supervisor

```bash
./scripts/dev-materialize.sh .dev-workspace coding-v1
# or
npm run check:binds    # build + node scripts/check-binds.mjs (needs workspace)
```

Uses `services/supervisor/scripts/materialize_harness_dev.py` under the hood.

### 3. Publish catalog changes

After editing `tool_sets/`, `prompt_packs/`, or `compositions/`:

```bash
npm run recompute-digests
```

Commit updated `digest` fields. Supervisor `verify_composition_catalog()` must pass at boot.

## Docker image

Build from **harness root** (not `images/`):

```bash
docker build -f images/Dockerfile -t pi-harness:coding-v1 .
docker inspect --format='{{index .RepoDigests 0}}' pi-harness:coding-v1
```

Pin the digest in `versions/<name>.yaml` → `image_digest`.

Always run sandboxes with `--init` so Pi subprocess zombies are reaped.

## Shipped manifests

| Version | Runtime | Sandbox | Mode |
|---|---|---|---|
| `coding-v1` | `harness` (Pi RPC) | required | sticky |
| `kg-v1` | `echo` (dev / lightweight) | none | stateless |

## Contracts

- [`../contracts/harness_binders.md`](../contracts/harness_binders.md) — `.yal/` file layout
- [`../contracts/composition.md`](../contracts/composition.md) — catalog digests
- Supervisor [`../services/supervisor/ARCHITECTURE.md`](../services/supervisor/ARCHITECTURE.md) — babysitter vs brain

## Tests

Supervisor unit tests cover materialization and extension reads:

```bash
cd ../services/supervisor
pytest tests/unit/test_harness_binders.py -v
```
