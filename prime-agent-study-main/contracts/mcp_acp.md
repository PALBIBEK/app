# MCP + ACP out-of-process providers (normative)

**QUEUE:** `oop-mcp-acp` (step 7)  
**Placement:** `supervisor/seams/providers/{mcp_tools,acp_subagent,composite_tools}.py`

## Principle

External MCP tool servers and ACP agents are **separate processes**. Supervisor
adapters implement existing `ToolProvider` / `SubagentProvider` Protocols — no
harness/Cordis types in journal/session/api.

## MCP tools (`McpToolProvider`)

| Item | Value |
|---|---|
| Config | `MCP_SERVER_URL` or factory `mcp_server_url=` |
| Wire | JSON-RPC 2.0 POST: `tools/list`, `tools/call` |
| Compose | `CompositeToolProvider` merges with catalog `ToolProvider` |

## ACP subagent (`AcpSubagentProvider`)

| Item | Value |
|---|---|
| Name | `"acp"` on `SubagentTransport` |
| Config | `ACP_BASE_URL` or factory `acp_base_url=` / `register_acp=True` |
| Wire (MVP) | `POST /v1/initialize`, `POST /v1/run`, `POST /v1/jobs` |
| Capabilities | spawn, fork, background |

Fail loud: HTTP 4xx/5xx, JSON-RPC `error`, unsupported capability (existing seam codes).

## Factory

Only `build_runtime_seams` / binders construct these concretes (P-3).

## Out of scope

- MCP stdio transport
- Per-tool `provider_ref` on catalog ToolDefinition rows
- Full ACP continuable session product

## Related

- [`seams.md`](./seams.md) — Protocol definitions
- [`subagent.md`](./subagent.md) — journal events for child lifecycle
