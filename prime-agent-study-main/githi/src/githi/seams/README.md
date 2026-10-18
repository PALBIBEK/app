# Seams package

Capability seams for githi: **ToolProvider**, **PromptAssembler**, **SubagentTransport**,
**ModelGatewayClient**.

## Config

Default wiring: `githi.seams.factory.build_default_seams()` then
`seams.bind_parent(agent)` (also invoked from `Agent` when no transport is injected).

| Knob | Where |
|---|---|
| Subagent provider name | `register_provider(name, provider)` — default `"in_process"` |
| Prompt text | Inject `PromptAssembler` on `Agent(..., prompt_assembler=...)` |
| Model access | Inject `gateway=` or wrap `LLMClient` via `LLMGatewayAdapter` |
| Tools catalog | Still `ToolRegistry` / `default_tools()` this slice; `InProcessToolProvider` adapts |

## Semantics

- **Definition** modules under `githi.seams.*` are `typing.Protocol` + dataclasses.
- **Providers** live in `githi.seams.providers` and are constructed only from the factory
  (or tests).
- **Consumers** (`Agent`, tools) bind by Protocol / façade — not by importing provider classes.
- Unsupported subagent capabilities raise `UnsupportedCapability` **before** child start.
- Continuable support is **method presence** (`start_continuable`), not a boolean flag.
- `AssembledPrompt.content_digest` is an optional content hash for tests — **not** a session catalog pin.

## Limitations

- No composition / catalog digests yet (`catalog-pins`).
- `default_tools()` remains the production tool list (`toolset-inject`).
- Vendor API keys may still enter the process (`model-gateway` removes them).
- First subagent provider is in-process only (`oop-mcp-acp` adds remote).

## Extension points

1. Implement a Protocol (Definition).
2. Add a Provider under `providers/` (or external package).
3. Register by name in `build_default_seams` / `register_provider`.
4. Inject via `Agent(..., subagent_transport=..., prompt_assembler=..., gateway=...)`.

Do **not** construct provider graphs inside `Agent.__init__`.
