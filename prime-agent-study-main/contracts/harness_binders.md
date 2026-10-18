# Harness binders — session file contract (QUEUE step 3)

**Placement:** host materializes under `session_dir/.yal/`; harness TS extensions consume.

## Why

Catalog pins + `RuntimeSeams` live in the supervisor process. Pi (and other
agent-cores) run in a sandbox and need a **stable, digest-backed file bind** so
extensions do not invent a parallel tool/prompt catalog.

## Files (written by supervisor)

| Path | Content |
|---|---|
| `.yal/pins.json` | `SessionPins` JSON — digests + ids only (no provider class names) |
| `.yal/system_prompt.txt` | UTF-8 text from `PromptAssembler.assemble` |
| `.yal/tools.json` | JSON array of `{ name, description, parameters }` from `ToolProvider.list_tools` |
| `.yal/memory_profile.json` | Compaction/recall settings when composition pins `MemoryProfile` |
| `.yal/memory_rpc.json` | Supervisor compaction/recall HTTP URLs (ContextMemory) |
| `.yal/memory_service.json` | Durable Memory Service bind when `MEMORY_SERVICE_URL` is set (Tier 1 adapter) |

## Harness YAML `tools:` map

The `tools:` block in `harness/versions/*.yaml` is **recovery classification only**
(`pure` / `idempotent` / `transactional` / `non_atomic`). It is **not** the
production ToolSet. Production tools come from composition digests →
`CatalogToolProvider` → `.yal/tools.json`.

## Extensions

| Extension | Behavior |
|---|---|
| `registerPrompt` | Reads `.yal/system_prompt.txt`; fail loud if missing when composition session expected |
| tools binder | Reads `.yal/tools.json` |
| `registerMemoryCompaction` | Reads `.yal/memory_profile.json`; Pi `session_before_compact` → gateway summarizer |
| `registerDurableMemory` | Reads `.yal/memory_service.json`; sync `pushEvent` + awaited `flush`/`start`/`resume` → Memory Service |
| gates / approvals | Supervisor-owned policy remains source of truth; extensions may no-op or bridge later |

## Fail loud

Missing required `.yal` files for a composition-pinned session → refuse start /
surface typed error — do not silently fall back to hardcoded prompt/tool lists.
