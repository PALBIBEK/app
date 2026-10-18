# Composition catalog (in-supervisor)

Content-addressed **PromptPack** + **ToolSet** + **AgentComposition** pins.
Session create resolves digests, persists `SessionPins`, and injects
`RuntimeSeams` via `bind_runtime_seams` → `build_runtime_seams`.

## Config

- In-process `CompositionCatalog` (offer / get / deprecate).
- Wire into `SessionManager(..., composition_catalog=...)`.
- Optional create args: `composition_id` / `composition_digest`.
- Durable field: `sessions.composition_pins` (JSON of digest strings only).

## Semantics

- Digest = `sha256:` + hex of canonical JSON **content** (status excluded).
- Offered bodies immutable; republish = new digest.
- Floating refs (`latest`) refused at create.
- Resume rebinds from stored pins — no floating re-resolve (`V-6`).

## Limitations

- In-supervisor only (not a sibling deployable).
- Tool invoke is stub for in-proc catalog tools; MCP via `McpToolProvider` when URL set.
- Model-gateway sibling documented separately.
- MCP/ACP adapters: [`contracts/mcp_acp.md`](../../../../../contracts/mcp_acp.md).
- SandboxProfile catalog rows: see [`contracts/sandbox_profile.md`](../../../../../contracts/sandbox_profile.md).

## Extension points

- `CompositionCatalog.offer` / `get` / `deprecate`
- `resolve_composition` → `SessionPins`
- `bind_runtime_seams` (sole Catalog* provider constructor for production-shaped wiring)
- `catalog.providers.CatalogPromptAssembler` / `CatalogToolProvider`

See `contracts/composition.md`.
