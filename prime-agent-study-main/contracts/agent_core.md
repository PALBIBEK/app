# Agent core — AgentRuntime seam

**QUEUE:** `agent-core-runtime` (step 0)  
**Placement:** in-supervisor module (`services/supervisor/src/supervisor/agent/`)

## Definition / Provider / Consumer

| Role | Artifact |
|---|---|
| **Definition** | `AgentRuntime` Protocol (`base.py`) + `AgentState` + envelope events + `Capabilities` / `get_capabilities` |
| **Provider(s)** | `GithiRuntime`, `EchoRuntime` under `agent/runtimes/` — registered **only** via `create_runtime` / `known_runtime_names` in `factory.py` |
| **Consumer(s)** | `session/manager.py`, harness `compat.check`, policy/budget observers of events |

Session / journal / API bind by runtime **name** (+ harness pin), never by concrete provider classes (`P-3`, `P-6`, `C-2`).

## RuntimeSeams — Consumer stance

Agent-core **consumes** independent plug services; it does **not own** tools, prompts, subagents, or the model gateway.

`RuntimeSeams` (`agent/seams.py`) is an optional frozen injection bag of typed Protocols | None:

```text
tool_provider: ToolProvider | None
prompt_assembler: PromptAssembler | None
subagent_transport: SubagentTransport | None
model_gateway: ModelGatewayClient | None
```

Runtimes **accept and store** seams; they **MUST NOT** construct providers themselves (`P-3`, `A-4`). Import Protocols from `supervisor.seams` (Definitions), never from `supervisor.seams.providers`. Defaults are built only via `build_runtime_seams`.

Session create **may** inject non-None `RuntimeSeams` from composition digests
(`catalog-pins` / [`composition.md`](./composition.md)) via
`resolve_composition` → `bind_runtime_seams` → `create_runtime(..., seams=)`.
Omit composition → `seams=None` (backward compatible).

Harness YAML `model:` is a **route hint** for the session's model — not a private model catalog inside agent-core (`G-2`). Gateway routing comes from the composition's plug bindings. See [`seams.md`](./seams.md).

## Fail-loud

| Case | Behavior |
|---|---|
| Unknown `runtime_name` | `ValueError` from factory **before** sandbox create |
| Unsupported harness `protocol_version` | `IncompatibleHarness` at spawn gate |
| Unregistered manifest `runtime` | `IncompatibleHarness` |
| Harness runtime without sandbox | `ValueError` on `start` |
| Unsupported `send` mode | `ValueError` |

## DeepSeek mapping (pattern only)

Thin `dsh-agent` Definition + `dsh-agent-loop` as concrete driver that injects llm/tools/prompt — **not** Cordis-in-supervisor (`A-12`).

## Related

- `contracts/seams.md` — typed Protocols agent-core consumes via RuntimeSeams
- `contracts/AGENT_CONTRACT.md` — wire events/commands; `get_capabilities` required at startup
- [`subagent.md`](./subagent.md) — `subagent_*` / `job_*` + session tree / SubagentOps
- Supervisor `ARCHITECTURE.md` — babysitter vs brain
