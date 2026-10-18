# Session memory contract (normative)

**QUEUE:** `memory-contract` + `context-memory-seam`  
**Placement:** `supervisor.agent.events` + `supervisor.seams.context_memory`  
**Seam:** `ContextMemoryProvider` ([`seams.md`](./seams.md))

## Principle

Session memory (context compaction + optional semantic recall) is a **harness-neutral
capability seam**. Supervisor journals compaction facts; providers own summarization,
trimming, and recall backends. `session/` / `journal/` / `api/` must not import
provider concretes.

**Not the same as** `MemorySubagentProvider` — that name is the in-process
**subagent** transport stub (`"memory"` provider), not semantic or compaction memory.

## Events

Wire types (snake_case): `compaction_start`, `compaction_end`, `memory_recall`.

Python payloads: `CompactionStart`, `CompactionEnd`, `MemoryRecall` in
`supervisor.agent.events`.

| Event | Fields | Pairing |
|---|---|---|
| `compaction_start` | `compaction_id`, `reason`, `tokens_before`, `first_kept_entry_id?` | pairs with `compaction_end` |
| `compaction_end` | **same** `compaction_id`, `stop_reason`, `tokens_after?`, `summary_digest?` | |

`stop_reason`: `ok` \| `error` \| `cancelled` \| `deferred` (`proceed_default` path).

| `memory_recall` | `block_id`, `source`, `token_estimate`, `injected` | log-only observability |

`reason`: `manual` \| `threshold` \| `overflow` (Pi / DeepSeek parity).

Unpaired `compaction_start` ⇒ compaction outcome unknown (same recovery signal as
unpaired `tool_start`).

## ContextMemoryProvider

Definition in `supervisor/seams/context_memory.py`:

| Method | Role |
|---|---|
| `estimate_tokens(messages)` | Cheap pressure estimate |
| `before_compact(ctx, preparation, reason)` | `CompactionDecision`: proceed_default, cancel, or custom result |
| `recall_for_turn(ctx, user_prompt)` | Optional semantic block (may be `None`) |
| `append_plug_state(ctx, custom_type, data)` | Extension-private durable entry id |

DTOs: `CompactionPreparation`, `CompactionResult`, `CompactionDecision`,
`RecallBlock`, `MemoryInvokeContext`.

## Default providers

| Provider | Package | Behavior |
|---|---|---|
| `NullContextMemoryProvider` | `seams/providers/null_context_memory.py` | proceed_default; no recall |
| `BasicCompactionProvider` | `seams/providers/basic_compaction.py` | Gateway summarizer + compaction helpers |
| `MarkdownRecallProvider` | `seams/providers/markdown_recall.py` | Markdown bullet recall |
| `McpSemanticRecallProvider` | `seams/providers/mcp_semantic_recall.py` | MCP `memory_search` recall backend |
| `CompositeContextMemoryProvider` | `seams/providers/composite_context_memory.py` | Compaction + recall overlay |

Constructed only via `build_runtime_seams(context_memory=...)`,
`build_context_memory_from_profile(profile, gateway)`, or `bind_runtime_seams`
(loads pinned `MemoryProfile` from catalog). No profile pin → Null.

## Ops bridge

`MemoryOps` (`session/memory_ops.py`):

- Accepts `ContextMemoryProvider` + `JournalWriter`
- Journals `CompactionStart` before `before_compact`
- Journals paired `CompactionEnd` (`ok` / `error` / `cancelled` / `deferred`)
- `recall(user_prompt)` calls `recall_for_turn`, journals `MemoryRecall` (`injected` true/false); **never fails the run**
- `SessionManager.compact_memory` / `recall_memory` delegate here

`unpaired_compaction_starts` → recovery `NEEDS_REVIEW` (same signal as unpaired tools).

## Fail-loud codes (future rows)

| Code | When |
|---|---|
| `COMPACTION_IN_PROGRESS` | Lock held (orphan `compaction_start`) |
| `COMPACTION_FAILED` | Summarizer error after `compaction_start` |
| `MEMORY_BACKEND_MISSING` | Profile pins MCP URL but provider not registered |
| `RECALL_DISABLED` | recall requested but profile says none |

## Related

- Agent introspection: [`AGENT_CONTRACT.md`](./AGENT_CONTRACT.md) (compaction pair)
- Research: `agent-platform-research/session-memory/`
- HTTP RPC: `POST /v1/sessions/{id}/memory/compact` and `POST /v1/sessions/{id}/memory/recall`
- Materialized bind: `.yal/memory_rpc.json` with `compact_url`, `recall_url`, optional `tenant_id`
- Pi hooks (`extensions/memory/hooks.ts`): prefer supervisor RPC when bind present; gateway summarize is fallback only; `session_before_turn` recall when `recall.enabled`
- Shipped: `coding-v1` pins `memory_profiles/default`
