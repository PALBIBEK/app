# Supervisor Architecture Overview

The **Supervisor** is the orchestration layer that manages agent sessions, enforces policies, and provides durability. Think of it as the **control plane** for your agent platform.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Frontend                                    │
│           (User selects: tools, prompts, sandbox, model)                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            API Layer                                     │
│    sessions.py │ tools.py │ approvals.py │ subagents.py │ memory.py    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SessionManager                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │   Catalog    │  │    Policy    │  │   Journal    │  │   Sandbox   │ │
│  │  (what runs) │  │ (guardrails) │  │  (history)   │  │(isolation)  │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Agent Runtimes                                  │
│              GithiRuntime │ HarnessRuntime │ EchoRuntime                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          RuntimeSeams (DI)                               │
│   ToolProvider │ PromptAssembler │ ModelGateway │ SubagentTransport    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. API Layer (`/api/`)

HTTP endpoints for external interaction:

| File | Purpose |
|------|---------|
| `sessions.py` | Create, send, abort, terminate sessions |
| `tools.py` | Direct tool invocation (outside agent loop) |
| `approvals.py` | Human-in-the-loop approval responses |
| `subagents.py` | Spawn/manage child agents |
| `memory.py` | Compaction and recall operations |
| `events.py` | SSE streaming of session events |
| `catalog.py` | Browse available compositions |
| `workspace.py` | File sync for durable workspaces |
| `health.py` | Readiness/liveness probes |

---

### 2. Session Management (`/session/`)

**`manager.py`** - The brain of the supervisor:

```python
class SessionManager:
    # Lifecycle
    async def create() → session_id      # Start new session
    async def send() → None              # Send user message
    async def resume() → ResumeResult    # Cold start from journal
    async def terminate() → None         # Cleanup session

    # Operations
    async def invoke_tool()              # Direct tool call
    async def spawn_child()              # Create subagent
    async def compact_memory()           # Summarize context
    async def respond_approval()         # Human approval response
```

**Key responsibilities:**
- Creates runtime with seams from catalog
- Provisions sandboxes
- Enforces budgets/quotas
- Manages approval flow
- Persists events to journal
- Handles cold resume/recovery

---

### 3. Policy Layer (`/policy/`)

**Guardrails that protect users and enforce limits:**

| File | What It Does |
|------|--------------|
| `budget.py` | Token/cost limits per session |
| `quotas.py` | Tenant-level resource limits |
| `rate_limit.py` | Request throttling |
| `approvals.py` | Human-in-the-loop broker |
| `guards.py` | Loop detection (repetition, oscillation, stalls) |
| `sandbox.py` | File effects policy (read-only, workspace, full) |
| `leader_lease.py` | Distributed locking |

**Example: Budget enforcement**
```python
# On every MessageEnd event with token usage:
state = await self._budget.record(session_id, usage)
if state.exceeded:
    await self._policy_abort(session_id, "BudgetExceeded")
```

---

### 4. Journal (`/journal/`)

**Append-only event log for durability:**

```
Session Timeline:
┌─────┬─────────────┬──────────────────────────────────┐
│ seq │ event_type  │ payload                          │
├─────┼─────────────┼──────────────────────────────────┤
│ 1   │ RunStart    │ {}                               │
│ 2   │ TurnStart   │ {}                               │
│ 3   │ ToolStart   │ {tool: "read", args: {...}}      │
│ 4   │ ToolEnd     │ {result: "file contents..."}     │
│ 5   │ MessageEnd  │ {content: "...", usage: {...}}   │
│ 6   │ TurnEnd     │ {stop_reason: "stop"}            │
│ 7   │ RunEnd      │ {messages: [...]}                │
└─────┴─────────────┴──────────────────────────────────┘
```

| File | Purpose |
|------|---------|
| `writer.py` | Append events to PostgreSQL |
| `reader.py` | Query session history |
| `sync.py` | Ensure event ordering |
| `serde.py` | Event serialization |
| `commands.py` | Command tracking |

**Why journals matter:**
- **Recovery**: Rebuild session state after crash
- **Audit**: Complete history of what happened
- **Replay**: Debug by replaying events

---

### 5. Catalog (`/catalog/`)

**The "app store" of agent components:**

```
AgentComposition
├── PromptPack      → System prompts, persona, instructions
├── ToolSet         → Available tools with schemas
├── SandboxProfile  → Where/how code runs (Docker, E2B, local)
├── MemoryProfile   → Compaction thresholds, recall backend
└── PlugBindings    → External service URLs (gateway, MCP)
```

| File | Purpose |
|------|---------|
| `types.py` | DTOs: PromptPack, ToolSet, SandboxProfile, etc. |
| `store.py` | Persistence for catalog artifacts |
| `resolve.py` | Look up composition by ID/digest |
| `binder.py` | Build RuntimeSeams from catalog pins |
| `digests.py` | Content-addressable hashing |
| `verify.py` | Validate artifact integrity |

**Session Pins** - Immutable snapshot at session creation:
```python
@dataclass
class SessionPins:
    composition_id: str
    composition_digest: str
    prompt_pack_digest: str
    tool_set_digest: str
    sandbox_profile_digest: str
    # ... pins never change during session
```

---

### 6. Sandbox (`/sandbox/`)

**Isolated execution environments:**

| Provider | Description |
|----------|-------------|
| `none.py` | No isolation (dev only) |
| `local_process.py` | Subprocess isolation |
| `local_docker.py` | Docker containers |
| `e2b.py` | E2B cloud sandboxes |

**Lifecycle:**
```
create() → start() → [exec commands] → pause() → resume() → destroy()
```

| File | Purpose |
|------|---------|
| `base.py` | Sandbox protocol definition |
| `factory.py` | Create sandbox by provider name |
| `pool/` | Pre-warmed sandbox pool for fast starts |
| `workspace_tree.py` | File I/O abstraction |
| `gate.py` | Permission checks |
| `reaper.py` | Cleanup orphaned sandboxes |

---

### 7. Harness (`/harness/`)

**Configuration for how an agent behaves:**

```python
@dataclass
class HarnessManifest:
    harness_version: str    # e.g., "githi/v1"
    runtime: str            # e.g., "githi"
    model: str              # e.g., "claude-sonnet-4-5"
    requires_sandbox: bool
    budget: BudgetSpec      # Token/cost limits
    guards: GuardSpec       # Loop detection config
    approvals: ApprovalSpec # Human-in-the-loop settings
```

| File | Purpose |
|------|---------|
| `manifest.py` | HarnessManifest dataclass |
| `resolver.py` | Look up harness by version string |
| `compat.py` | Check runtime capabilities |
| `bind_files.py` | Materialize composition to session workspace |
| `spawn.py` | Subprocess harness execution |

---

### 8. Temporal Workflows (`/temporal/`)

**Durable workflow execution for long-running sessions:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    AgentSessionWorkflow                          │
│                                                                  │
│   State:                                                         │
│   - pending_messages: []                                         │
│   - pending_approval: ApprovalRequest | None                     │
│   - turn_count: int                                              │
│   - status: active | processing | awaiting_approval | stopped   │
│                                                                  │
│   Signals:                        Queries:                       │
│   - send_message(text)           - get_status()                 │
│   - approve(id, decision)        - get_pending_approval()       │
│   - stop()                       - get_turn_count()             │
│   - pause() / resume()                                          │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     AgentTurnWorkflow                            │
│              (Child workflow for each turn)                      │
│                                                                  │
│   - Calls LLM via activity                                       │
│   - Executes tools via activities                                │
│   - Checkpoints progress                                         │
│   - Returns result or approval request                          │
└─────────────────────────────────────────────────────────────────┘
```

**Why Temporal?**
- Sessions can run for hours/days
- Survives process restarts, deployments, crashes
- Human approvals can wait indefinitely
- Automatic retry on failures

---

### 9. Observability (`/obs/`)

**Telemetry and tracing:**

- OpenTelemetry integration
- Span projection from journal events
- Session-level trace aggregation

---

### 10. Auth (`/auth/`)

**Authentication and authorization:**

- Tenant isolation
- Scope-based access control
- Virtual key management

---

## Data Flow: User Sends a Message

```
1. HTTP POST /sessions/{id}/send
         │
         ▼
2. SessionManager.send()
   ├─ Check budget limits
   ├─ Acquire session lease
   └─ Forward to runtime
         │
         ▼
3. GithiRuntime.send()
   └─ AgentLoop.run_turn()
      ├─ Call ModelGateway (LLM)
      ├─ Execute tools via ToolProvider
      ├─ Emit events (ToolStart, ToolEnd, etc.)
      └─ Return RunEnd with messages
         │
         ▼
4. SessionManager._fan_out()
   ├─ Write events to Journal
   ├─ Publish to LiveHub (SSE)
   ├─ Check guards (loop detection)
   ├─ Record budget usage
   └─ Sync workspace if dirty
         │
         ▼
5. Client receives SSE events
```

---

## Composable Harness Flow

**Your frontend scenario:**

```
Frontend                    Backend
   │                           │
   │  1. Show catalog          │
   │◄──────────────────────────│ GET /catalog/compositions
   │                           │
   │  2. User picks:           │
   │     - PromptPack: "coder" │
   │     - ToolSet: "file_ops" │
   │     - Sandbox: "docker"   │
   │                           │
   │  3. Create composition    │
   │──────────────────────────►│ POST /catalog/compositions
   │                           │   → Returns composition_id + digest
   │                           │
   │  4. Create session        │
   │──────────────────────────►│ POST /sessions
   │                           │   composition_id: "abc123"
   │                           │
   │                           │   Backend:
   │                           │   - Resolve composition → SessionPins
   │                           │   - Build RuntimeSeams from pins
   │                           │   - Create sandbox
   │                           │   - Start GithiRuntime
   │                           │   → Returns session_id
   │                           │
   │  5. Send messages         │
   │──────────────────────────►│ POST /sessions/{id}/send
   │                           │
   │  6. Stream events         │
   │◄──────────────────────────│ SSE /sessions/{id}/events
```

---

## File Structure Summary

```
supervisor/
├── api/                 # HTTP endpoints
├── agent/               # Runtime implementations
│   ├── runtimes/
│   │   ├── githi/       # Main production runtime
│   │   ├── harness/     # Evaluation harness
│   │   └── echo/        # Test runtime
│   ├── seams.py         # DI container
│   └── factory.py       # Runtime creation
├── catalog/             # Composition catalog
├── harness/             # Harness manifests
├── journal/             # Event persistence
├── policy/              # Budgets, quotas, guards
├── sandbox/             # Isolation providers
├── seams/               # Seam interfaces + providers
├── session/             # Session lifecycle
├── temporal/            # Durable workflows
├── auth/                # Authentication
├── db/                  # Database models
├── obs/                 # Observability
└── telemetry_contract/  # Telemetry adapters
```

---

## Key Design Principles

1. **Composability**: Mix and match prompts, tools, sandboxes
2. **Durability**: Journal everything, recover from crashes
3. **Isolation**: Sandboxes prevent cross-session contamination
4. **Policy enforcement**: Budgets, approvals, guards enforced at platform level
5. **Content-addressed**: Digests ensure immutable, reproducible sessions
6. **Seam-based DI**: Swap implementations without changing runtime code
