# Architecture Overview: Composable Agent Platform

This is a **dependency injection-based agent platform** where each runtime can compose different components (seams) to customize its behavior.

## Core Concept: RuntimeSeams (Dependency Injection Container)

**`seams.py:20-27`** - The `RuntimeSeams` dataclass is the heart of composability:

```
RuntimeSeams:
├── tool_provider      → What tools the agent can use
├── prompt_assembler   → How system prompts are built
├── subagent_transport → How to spawn/communicate with child agents
├── model_gateway      → Which LLM to call (OpenAI-compat interface)
└── context_memory     → Long-term memory provider
```

Each runtime can select **any implementation** for each seam, making the platform highly modular.

---

## Runtime Factory Pattern

**`factory.py:34-69`** - Single entry point `create_runtime()`:
- Takes a runtime name (`echo`, `harness`, `githi`)
- Injects the provided `RuntimeSeams`
- Lazy-imports to keep startup fast

```python
create_runtime("githi", seams=my_seams, model="claude-sonnet-4-5")
```

---

## GithiRuntime - The Main Runtime

**`runtimes/githi/runtime.py:40-257`** - Orchestrator with PostgreSQL persistence:

### Lifecycle

1. `start()` → Creates session, acquires from PostgreSQL (if available), initializes tool provider
2. `send(message)` → Acquires lease (QM pattern), runs turn via `AgentLoop`, persists messages
3. `stop()` → Cleanup

### Key Features

- **Lease-based concurrency** - Only one turn runs at a time (`acquire_lease`/`release_lease`)
- **Durable by default** - Messages persisted to PostgreSQL
- **Session isolation** - Each session has its own history

---

## AgentLoop - The Turn Execution Engine

**`runtimes/githi/loop.py:223-738`** - Where the actual LLM interaction happens:

### `run_turn()` Flow

```
User Message → RunStart
    ↓
TurnStart
    ↓
Gateway.complete(messages, tools) ←─┐
    ↓                                │
Extract tool_calls                   │
    ↓                                │
For each tool_call:                  │
  ├─ ToolStart event                 │
  ├─ tool_provider.invoke()          │
  └─ ToolEnd event                   │
    ↓                                │
If more tools needed ────────────────┘
    ↓
MessageEnd (assistant response)
    ↓
TurnEnd → RunEnd
```

### Advanced Features in `run_loop()`

- **Turn budget warnings** - Tells model how many turns remain
- **Force answer on final turn** - No tools, must answer with what it knows
- **Steering messages** - User can redirect mid-turn
- **Follow-up messages** - Restart after completion
- **Compaction** - Summarizes context when it gets too long

---

## Hooks System - Policy Enforcement

**`runtimes/githi/hooks.py:71-156`** - External control without model self-policing:

| Hook | Purpose |
|------|---------|
| `before_tool_call` | **Block or rewrite** tool calls (security gate!) |
| `after_tool_call` | Observe results |
| `transform_context` | Modify messages before LLM |
| `should_stop` | Early termination |
| `get_steering_messages` | Mid-turn corrections |
| `get_follow_up_messages` | Post-completion restarts |

### Key Insight

> "A rule the model is merely told about is a suggestion; a rule enforced here is a rule."

---

## Tool System

### GithiToolProvider

**`runtimes/githi/tools/provider.py:36-129`** - Bridges the seam:
- Lists available tools via `list_tools()`
- Invokes tools via `invoke(ctx, name, args)`
- Strips sensitive args (`scope_id`, `tenant_id`, etc.) that model shouldn't control

### Tool Registry

**`runtimes/githi/tools/registry.py:53-72`** - Singleton registry with built-in tools:
- `ReadTool`, `WriteTool`, `LsTool`, `GrepTool` (file operations)

---

## Seam Interfaces (Protocols)

All seams are **Python Protocols** (structural typing):

| Seam | Interface | Purpose |
|------|-----------|---------|
| `ToolProvider` | `list_tools()`, `invoke()` | Tool execution |
| `PromptAssembler` | `assemble(bindings)` | System prompt generation |
| `SubagentTransport` | `start_one_shot()`, `start_fork()`, etc. | Child agent management |
| `ModelGatewayClient` | `complete()`, `stream()` | LLM API abstraction |

---

## How It All Connects

```
┌──────────────────────────────────────────────────────────────┐
│                         SessionManager                        │
│                              │                                │
│              create_runtime("githi", seams=...)               │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                        GithiRuntime                           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                     RuntimeSeams                        │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │  │
│  │  │ToolProvider │ │PromptAssem │ │ModelGateway │       │  │
│  │  └─────────────┘ └─────────────┘ └─────────────┘       │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│                         AgentLoop                             │
│                     ┌────────┴────────┐                       │
│                   Hooks            Tools                      │
│             (policy gates)    (file ops, etc.)                │
└──────────────────────────────────────────────────────────────┘
```

---

## Design Benefits

This architecture allows you to:

1. **Swap LLM providers** by changing `model_gateway`
2. **Add custom tools** by implementing `ToolProvider`
3. **Change prompt strategies** via `PromptAssembler`
4. **Enforce security policies** through `Hooks`
5. **Run subagents** via `SubagentTransport`

---

## File Structure

```
agent/
├── __init__.py
├── base.py              # AgentRuntime protocol, AgentState, AgentPhase
├── seams.py             # RuntimeSeams DI container
├── factory.py           # create_runtime() entry point
├── capabilities.py      # Runtime capability declarations
├── events.py            # Event types (RunStart, ToolEnd, etc.)
├── stream.py            # EventStream for async event delivery
├── errors.py            # Agent-specific exceptions
│
└── runtimes/
    ├── __init__.py
    │
    ├── echo/            # Simple test runtime
    │   └── runtime.py
    │
    ├── harness/         # Evaluation harness runtime
    │   ├── runtime.py
    │   ├── state.py
    │   ├── commands.py
    │   ├── framing.py
    │   └── translate.py
    │
    └── githi/           # Main production runtime
        ├── runtime.py       # GithiRuntime orchestrator
        ├── loop.py          # AgentLoop turn execution
        ├── hooks.py         # Policy enforcement hooks
        ├── session.py       # Session management
        ├── session_store.py # PostgreSQL persistence
        ├── state.py         # MutableAgentState
        ├── messages.py      # Message format conversion
        ├── compaction.py    # Context summarization
        ├── events.py        # Githi-specific events
        ├── sandbox.py       # Sandbox integration
        │
        └── tools/
            ├── __init__.py
            ├── base.py          # Tool base class
            ├── provider.py      # GithiToolProvider
            ├── registry.py      # Tool registry singleton
            ├── paths.py         # Path utilities
            ├── workspace_paths.py
            │
            └── file/            # Built-in file tools
                ├── __init__.py
                ├── read.py
                ├── write.py
                ├── ls.py
                └── grep.py
```
