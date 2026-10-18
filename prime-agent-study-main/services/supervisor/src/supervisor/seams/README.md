# supervisor.seams

Capability seam Definitions + first in-process providers for YAL agent-platform.

## Config

- Default bag: `build_runtime_seams()` in `factory.py` (sole name→impl map).
- Optional override kwargs: `tool_provider`, `prompt_assembler`, `subagent_transport`, `model_gateway`, `context_memory`.
- Optional HTTP gateway: `gateway_base_url=` / env `GATEWAY_BASE_URL` → `HttpModelGatewayClient`.
- Optional MCP: `mcp_server_url=` / env `MCP_SERVER_URL` → `McpToolProvider` (+ compose).
- Optional ACP: `acp_base_url=` / env `ACP_BASE_URL` / `register_acp=True` → `"acp"` provider.
- Inject into runtimes via `create_runtime(..., seams=...)`.

## Semantics

| Protocol | Role |
|---|---|
| `ToolProvider` | List / invoke tools without knowing the registry class |
| `PromptAssembler` | Bindings → assembled system prompt text |
| `SubagentTransport` | Named multi-provider registry + disposer; fail-loud before start |
| `ModelGatewayClient` | OpenAI-compat `complete` / `stream` / `close` (no vendor SDK authority) |
| `ContextMemoryProvider` | Compaction + optional recall (`before_compact`, `recall_for_turn`) |

Fail-loud codes: `UNSUPPORTED_CAPABILITY`, `PROVIDER_MISSING`, `PROVIDER_CONFLICT`, `BAD_ARGS`, `DEPTH_EXCEEDED`.

## Limits

- Default bag is still in-process fakes; production HTTP is opt-in via URL.
- Continuable = method presence on provider (F-5).
- Sibling deployable docs: [`contracts/model_gateway.md`](../../../../../contracts/model_gateway.md), [`contracts/memory.md`](../../../../../contracts/memory.md).

## Extension points

- Add a provider under `providers/` and register it via `build_runtime_seams` or `SubagentTransport.register_provider`.
- Consumers bind Protocols from this package; never import `providers.*` into journal/session/api/runtimes.
