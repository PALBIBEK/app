# Composition catalog + session pins (normative)

**QUEUE:** `catalog-pins` (step 2)  
**Placement:** in-supervisor module `services/supervisor/src/supervisor/catalog/`  
**Law:** digests are content-addressed; sessions store digests, never floating tags.

## PlugArtifact envelope

```text
PlugArtifact {
  name: str
  semver: str
  digest: str                 # "sha256:" + 64 lowercase hex — pin this
  protocol_version: int
  status: draft | offered | deprecated
}
```

Digest = `sha256:` + hex of canonical UTF-8 JSON **content body** (sorted keys, no
insignificant whitespace). **Status is metadata** — not part of the digest body.
Republish = new digest (`V-3`). Offered bodies are immutable in place.

## PromptPack

```text
PromptPack : PlugArtifact {
  persona: str | None
  sections: [{ id, order, text, complete: bool }]
  vars: [str]
}
```

## ToolSet

```text
ToolDefinition {
  name: str
  description: str
  parameters: dict            # JSON Schema
  digest: str
  tool_class?: str            # optional hint; harness recovery map remains recovery authority
}

ToolSet : PlugArtifact {
  tools: { tool_name → ToolDefinition.digest }
  definitions?: { tool_name → ToolDefinition }   # in-proc MVP embedding
}
```

## AgentComposition

```text
AgentComposition : PlugArtifact {
  harness_version: str
  prompt_pack: { id, digest }
  tool_set: { id, digest }
  sandbox_profile?: { id, digest }   # optional; overrides deploy SANDBOX_PROVIDER
  # model_route / subagent_bindings — later QUEUE rows
}
```

## SessionPins (durable, provider-type-free)

```text
SessionPins {
  composition_id: str
  composition_digest: str
  harness_version: str
  prompt_pack_id: str
  prompt_pack_digest: str
  tool_set_id: str
  tool_set_digest: str
  sandbox_profile_id?: str
  sandbox_profile_digest?: str
}
```

Persisted on `sessions.composition_pins` JSON. Resume re-binds from these digests
(`V-6`) — it must **not** re-resolve a floating catalog tip.

Harness binders materialize `.yal/` files from these pins — see
[`harness_binders.md`](./harness_binders.md).

## CompositionCatalog

```text
offer(artifact) → artifact_with_digest
get(kind, id, digest, *, require_offered=False) → artifact | raise
resolve_composition(id | digest) → SessionPins
deprecate(kind, id, digest) → None         # status only; never rewrite body
bind_runtime_seams(pins, catalog) → RuntimeSeams
```

Handshake at session create (when composition is supplied):

1. Refuse floating refs (`latest`, `:latest`, `@latest`) → `FLOATING_REF`
2. Load composition; require `offered`
3. Verify PromptPack + ToolSet digests; require `offered`
4. Persist `SessionPins`; inject `RuntimeSeams` via binder → `create_runtime(..., seams=)`
5. Omit composition → today's harness-only path (`seams=None`)

## Fail-loud codes

| Code | When |
|---|---|
| `DIGEST_MISMATCH` | Stored bytes ≠ requested digest |
| `DIGEST_MISSING` | Unknown id/digest |
| `NOT_OFFERED` | draft/deprecated at create |
| `FLOATING_REF` | `latest` / bare mutable tag without digest |
| `COMPOSITION_INCOMPLETE` | Required PromptPack or ToolSet pin absent |
| `IMMUTABLE_ARTIFACT` | In-place body mutation attempted |

## Related

- [`seams.md`](./seams.md) — catalog pins feed `PromptAssembler` / `ToolProvider`
- [`agent_core.md`](./agent_core.md) — session create may inject non-None `RuntimeSeams`
- Harness YAML `tools:` remains **recovery classification**, not the production ToolSet
