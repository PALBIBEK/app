# Sandbox profile (normative)

**QUEUE:** `sandbox-profile` (step 6)  
**Placement:** `supervisor/catalog/` + `supervisor/sandbox/resolve.py` + session create

## Principle

- **Deploy default:** `SANDBOX_PROVIDER` env / supervisor config
- **Composition override:** digest-pinned `SandboxProfile` on `AgentComposition`
- Factory (`sandbox/factory.py`) is the **only** place provider names map to impls

## SandboxProfile

```text
SandboxProfile : PlugArtifact {
  provider: none | local_process | local_docker | e2b
  file_effects: read_only | workspace_write | danger_full_access
  approval_default: ask | never
  sticky_eligible: bool
  image_digest: string | null    # overrides harness manifest when set
}
```

## SessionPins extension

```text
sandbox_profile_id: str | null
sandbox_profile_digest: str | null
```

Resume reloads profile from pins (`V-6`) — no floating re-resolve.

## Fail loud

| Case | Behavior |
|---|---|
| `requires_sandbox` + profile `provider=none` | `IncompatibleHarness` |
| `!requires_sandbox` + profile with real provider | `IncompatibleHarness` |
| Missing provider / image when sandbox required | `RuntimeError` at create |

## Harness materialization

When pinned, session workspace gets `.yal/sandbox_profile.json` (metadata for extensions).

## Provider network / egress (D10)

| Provider | Network model | Operator expectations |
|---|---|---|
| `local_process` | Host network (dev only) | No isolation; **forbidden** when `MULTI_TENANT_STRICT=true`. Env scrub strips vendor/gateway secrets. |
| `local_docker` | `network_mode=none` by default (no egress) | Control-plane injects only `vk_*` gateway creds when needed. For DNS/gateway egress use K8s `deploy/k8s/networkpolicy-sandbox-default-deny.yaml` + bridge mode in a later profile variant. |
| `e2b` | **SaaS-managed egress** (E2B cloud VM) | Use for shared/multi-tenant deploys (`MULTI_TENANT_STRICT=true`). Egress is enforced by E2B platform policy, not supervisor code. **Never** pass `OPENAI_*`, `GATEWAY_TOKEN`, or DB URLs in sandbox env — `sanitize_sandbox_env` strips them; agent receives `vk_*` only via control-plane inject. Document expected allowlist (DNS + model-gateway) in deploy README. |
| `none` | N/A | No sandbox FS; tools requiring `/workspace` fail loud. |

Harness YAML `sandbox.allow_network` / `allow_subprocess` remain **advisory** until profile + provider enforce; production controls are `SandboxProfile` + NetworkPolicy + provider defaults above.

## Related

- [`composition.md`](./composition.md) — `AgentComposition.sandbox_profile` pin
- `supervisor/sandbox/factory.py` — provider implementations
