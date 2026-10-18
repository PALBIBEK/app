# Capability seams (normative)

Harness-neutral Definitions for capability plugs. Supervisor and other consumers
depend on these Protocol names and fail-loud codes — **not** on concrete provider
classes.

**Agent-core Consumer:** `AgentRuntime` (see [`agent_core.md`](./agent_core.md))
**consumes** these Protocols via optional `RuntimeSeams` handles. It must **not
construct** providers (P-3, A-4).

Package: `supervisor.seams` under
`YAL_AGENT_SERVICE/agent-platform/services/supervisor/`.

## Protocols

| Name | Methods |
|---|---|
| `ToolProvider` | `list_tools()` / `invoke(ctx, name, args) → ToolResult` |
| `PromptAssembler` | `assemble(PromptBindings) → AssembledPrompt` |
| `SubagentTransport` | Named multi-provider registry; `register_provider` → disposer; spawn / fork / background / continuable |
| `ModelGatewayClient` | OpenAI-compat `complete` / `stream` / `close` |
| `ContextMemoryProvider` | `estimate_tokens` / `before_compact` / `recall_for_turn` / `append_plug_state` |

## Fail-loud codes

| Code | When |
|---|---|
| `UNSUPPORTED_CAPABILITY` | Request exceeds provider advertised capabilities (before child start) |
| `PROVIDER_MISSING` | Named provider not registered (or disposed) |
| `PROVIDER_CONFLICT` | Duplicate `register_provider` without dispose |
| `BAD_ARGS` | Invalid request arguments before provider start |
| `DEPTH_EXCEEDED` | Delegation depth cap exceeded |

## Placement

- Definitions + first in-process providers: **library** under supervisor (`supervisor/seams/`)
- Composition root: `build_runtime_seams` (sole default concrete map) + later binders
- Supervisor journal / session / API: **must not** import provider concrete types
- Cordis / githi loader: **must not** be embedded in the supervisor
- githi `githi.seams` is **reference only**

## Related

- Model Gateway deployable: [`model_gateway.md`](./model_gateway.md)
- Subagent wire + tree: [`subagent.md`](./subagent.md)
- Session memory + compaction: [`memory.md`](./memory.md)
- Out-of-process MCP + ACP adapters: [`mcp_acp.md`](./mcp_acp.md)

## Catalog digests

PromptPack / ToolSet / AgentComposition digests and session pin handshake are
normative in [`composition.md`](./composition.md). Catalog-backed providers are
constructed only via `bind_runtime_seams` → `build_runtime_seams` (P-3).
