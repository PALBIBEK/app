# Seams Architecture

The seams folder implements a **Dependency Injection** pattern with **Protocol-based interfaces** and **pluggable providers**.

---

## Folder Structure (Organized)

```
seams/
├── __init__.py                 → Public exports (re-exports from subfolders)
│
├── interfaces/                 → Protocol definitions (contracts)
│   ├── __init__.py
│   ├── tool_provider.py        → ToolProvider protocol
│   ├── model_gateway.py        → ModelGatewayClient protocol
│   ├── prompt_assembler.py     → PromptAssembler protocol
│   ├── subagent_transport.py   → SubagentTransport protocol
│   └── context_memory.py       → ContextMemoryProvider protocol
│
├── core/                       → Shared types, errors, factory
│   ├── __init__.py
│   ├── types.py                → DTOs (ToolResult, ChatMessage, etc.)
│   ├── errors.py               → Error types
│   └── factory.py              → build_runtime_seams()
│
├── providers/                  → Concrete implementations (by provider type)
│   ├── __init__.py
│   │
│   ├── tools/                  → ToolProvider implementations
│   │   ├── __init__.py
│   │   ├── null_tools.py       → NullToolProvider (no-op)
│   │   ├── mcp_tools.py        → McpToolProvider (MCP server)
│   │   └── composite_tools.py  → CompositeToolProvider (merge)
│   │
│   ├── gateway/                → ModelGatewayClient implementations
│   │   ├── __init__.py
│   │   ├── fake_gateway.py     → FakeModelGatewayClient (testing)
│   │   ├── http_gateway.py     → HttpModelGatewayClient (real HTTP)
│   │   └── virtual_key_client.py → HttpVirtualKeyClient (key minting)
│   │
│   ├── prompt/                 → PromptAssembler implementations
│   │   ├── __init__.py
│   │   └── static_prompt.py    → StaticPromptAssembler
│   │
│   ├── subagent/               → SubagentTransport implementations
│   │   ├── __init__.py
│   │   ├── memory_subagent.py  → LabEchoSubagentProvider (testing)
│   │   ├── acp_subagent.py     → AcpSubagentProvider (ACP)
│   │   └── nested_session.py   → NestedSessionSubagentProvider
│   │
│   ├── memory/                 → ContextMemoryProvider implementations
│   │   ├── __init__.py
│   │   ├── null_memory.py      → NullContextMemoryProvider
│   │   ├── basic_compaction.py → BasicCompactionProvider
│   │   ├── markdown_recall.py  → MarkdownRecallProvider
│   │   ├── mcp_recall.py       → McpSemanticRecallProvider
│   │   ├── durable_recall.py   → DurableRecallProvider
│   │   └── composite_memory.py → CompositeContextMemoryProvider
│   │
│   ├── workspace/              → WorkspaceStore implementations
│   │   ├── __init__.py
│   │   ├── memory_workspace_store.py  → MemoryWorkspaceStore (testing)
│   │   ├── postgres_workspace_store.py → PostgresWorkspaceStore (durable)
│   │   └── workspace_store_factory.py  → create_workspace_store()
│   │
│   ├── storage/                → ObjectStorage implementations
│   │   ├── __init__.py
│   │   ├── memory_object_storage.py → MemoryObjectStorage (testing)
│   │   ├── s3_object_storage.py     → S3ObjectStorage (S3/SeaweedFS)
│   │   └── object_storage_factory.py → create_object_storage()
│   │
│   └── authz/                  → GrantStore implementations
│       ├── __init__.py
│       ├── memory_grant_store.py    → MemoryGrantStore (testing)
│       ├── postgres_grant_store.py  → PostgresGrantStore (durable)
│       └── grant_store_factory.py   → create_grant_store()
│
└── services/                   → Service abstractions (Protocols + helpers)
    ├── __init__.py
    ├── workspace_store.py      → WorkspaceStore protocol + DTOs
    ├── object_storage.py       → ObjectStorage protocol + DTOs
    ├── authz.py                → AuthzService, GrantStore protocols + Principal
    ├── authz_service.py        → StoreAuthzService implementation
    ├── authorized_workspace.py → authorized_hydrate/sync helpers
    ├── virtual_keys.py         → VirtualKeyClient protocol
    ├── session_workspace.py    → SessionWorkspaceService
    ├── session_gateway.py      → attach_minted_gateway helper
    ├── workspace_hydrator.py   → WorkspaceHydrator protocol + impl
    └── memory_service_client.py → Memory Service HTTP client
```

---

## Migration Map

| Current Location | New Location |
|------------------|--------------|
| `seams/tool_provider.py` | `seams/interfaces/tool_provider.py` |
| `seams/model_gateway.py` | `seams/interfaces/model_gateway.py` |
| `seams/prompt_assembler.py` | `seams/interfaces/prompt_assembler.py` |
| `seams/subagent_transport.py` | `seams/interfaces/subagent_transport.py` |
| `seams/context_memory.py` | `seams/interfaces/context_memory.py` |
| `seams/types.py` | `seams/core/types.py` |
| `seams/errors.py` | `seams/core/errors.py` |
| `seams/factory.py` | `seams/core/factory.py` |
| `seams/providers/null_tools.py` | `seams/providers/tools/null_tools.py` |
| `seams/providers/mcp_tools.py` | `seams/providers/tools/mcp_tools.py` |
| `seams/providers/composite_tools.py` | `seams/providers/tools/composite_tools.py` |
| `seams/providers/fake_gateway.py` | `seams/providers/gateway/fake_gateway.py` |
| `seams/providers/http_gateway.py` | `seams/providers/gateway/http_gateway.py` |
| `seams/providers/static_prompt.py` | `seams/providers/prompt/static_prompt.py` |
| `seams/providers/memory_subagent.py` | `seams/providers/subagent/memory_subagent.py` |
| `seams/providers/acp_subagent.py` | `seams/providers/subagent/acp_subagent.py` |
| `seams/providers/nested_session_subagent.py` | `seams/providers/subagent/nested_session.py` |
| `seams/providers/null_context_memory.py` | `seams/providers/memory/null_memory.py` |
| `seams/providers/basic_compaction.py` | `seams/providers/memory/basic_compaction.py` |
| `seams/providers/markdown_recall.py` | `seams/providers/memory/markdown_recall.py` |
| `seams/providers/mcp_semantic_recall.py` | `seams/providers/memory/mcp_recall.py` |
| `seams/providers/composite_context_memory.py` | `seams/providers/memory/composite_memory.py` |
| `seams/workspace_store.py` | `seams/services/workspace_store.py` |
| `seams/object_storage.py` | `seams/services/object_storage.py` |
| `seams/authz.py` | `seams/services/authz.py` |
| `seams/virtual_keys.py` | `seams/services/virtual_keys.py` |
| `seams/session_workspace.py` | `seams/services/session_workspace.py` |

---

## Import Changes After Migration

```python
# BEFORE
from supervisor.seams.tool_provider import ToolProvider
from supervisor.seams.types import ToolResult
from supervisor.seams.providers.mcp_tools import McpToolProvider

# AFTER
from supervisor.seams.interfaces import ToolProvider
from supervisor.seams.core import ToolResult
from supervisor.seams.providers.tools import McpToolProvider

# OR via main __init__.py (recommended)
from supervisor.seams import ToolProvider, ToolResult, McpToolProvider
```

---

## The 5 Core Providers

| # | Provider | Protocol File | Purpose |
|---|----------|---------------|---------|
| 1 | **ToolProvider** | `tool_provider.py` | Execute tools (read, write, grep, etc.) |
| 2 | **ModelGatewayClient** | `model_gateway.py` | Call LLM APIs (Claude, GPT, etc.) |
| 3 | **PromptAssembler** | `prompt_assembler.py` | Build system prompts |
| 4 | **SubagentTransport** | `subagent_transport.py` | Spawn/manage child agents |
| 5 | **ContextMemoryProvider** | `context_memory.py` | Compaction + semantic recall |

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                      factory.py                                  │
│                  build_runtime_seams()                          │
│                                                                  │
│   Takes: optional overrides for each seam                       │
│   Returns: RuntimeSeams with all 5 seams filled                 │
│                                                                  │
│   Logic:                                                         │
│   1. tool_provider → _maybe_mcp_tools() → NullToolProvider      │
│   2. prompt_assembler → StaticPromptAssembler                   │
│   3. model_gateway → _resolve_gateway() → Http or Fake          │
│   4. subagent_transport → _build_subagent_transport()           │
│   5. context_memory → NullContextMemoryProvider                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     RuntimeSeams                                 │
│  (from supervisor/agent/seams.py - the DI container)           │
│                                                                  │
│   tool_provider: ToolProvider                                   │
│   prompt_assembler: PromptAssembler                             │
│   subagent_transport: SubagentTransport                         │
│   model_gateway: ModelGatewayClient                             │
│   context_memory: ContextMemoryProvider                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Current Sync/Async Status

| Provider | Current | Target | Why Change |
|----------|---------|--------|------------|
| ToolProvider | **SYNC** | ASYNC | Tools do I/O (file, network) |
| ModelGatewayClient | **SYNC** | ASYNC | LLM calls are network I/O |
| PromptAssembler | SYNC | SYNC | CPU-bound string assembly |
| SubagentTransport | Mixed | ASYNC | Spawns network calls |
| ContextMemoryProvider | **SYNC** | ASYNC | May call LLM for summarization |

---

## Files to Work On (In Order)

### Priority 1: Core Interfaces

| File | Purpose | Action |
|------|---------|--------|
| `types.py` | All DTOs | Read first, understand data shapes |
| `tool_provider.py` | Tool execution contract | **Make async** |
| `model_gateway.py` | LLM API contract | **Make async** |
| `context_memory.py` | Memory operations | **Make async** |

### Priority 2: Factory

| File | Purpose | Action |
|------|---------|--------|
| `factory.py` | Builds RuntimeSeams | Update after interface changes |
| `errors.py` | Error types | Review |

### Priority 3: Provider Implementations

Update these to match new async interfaces:

| File | Purpose |
|------|---------|
| `providers/http_gateway.py` | Real HTTP calls |
| `providers/mcp_tools.py` | MCP protocol |
| `providers/basic_compaction.py` | LLM summarization |
| `providers/acp_subagent.py` | ACP protocol |

---

## Provider 1: ToolProvider

### Interface (`tool_provider.py`)

```python
# CURRENT (sync)
class ToolProvider(Protocol):
    def list_tools(self) -> list[ToolDescriptor]: ...
    def invoke(self, ctx: ToolInvokeContext, name: str, args: dict) -> ToolResult: ...

# TARGET (async)
class ToolProvider(Protocol):
    def list_tools(self) -> list[ToolDescriptor]: ...  # Keep sync (just returns data)
    async def invoke(self, ctx: ToolInvokeContext, name: str, args: dict) -> ToolResult: ...
```

### Implementations

| Provider | File | Description |
|----------|------|-------------|
| NullToolProvider | `providers/null_tools.py` | Returns empty, no-op |
| McpToolProvider | `providers/mcp_tools.py` | Calls MCP server |
| CompositeToolProvider | `providers/composite_tools.py` | Merges multiple providers |

### Used By

- `agent/runtimes/githi/tools/provider.py` → GithiToolProvider wraps this
- `session/manager.py` → Direct tool invocation

---

## Provider 2: ModelGatewayClient

### Interface (`model_gateway.py`)

```python
# CURRENT (sync)
class ModelGatewayClient(Protocol):
    def complete(self, messages, tools, *, model) -> Completion: ...

# TARGET (async)
class ModelGatewayClient(Protocol):
    async def complete(self, messages, tools, *, model) -> Completion: ...
    async def stream(self, messages, tools, *, model) -> AsyncIterator[StreamEvent]: ...
```

### Implementations

| Provider | File | Description |
|----------|------|-------------|
| FakeModelGatewayClient | `providers/fake_gateway.py` | Testing/mock |
| HttpModelGatewayClient | `providers/http_gateway.py` | Real HTTP calls |

### Used By

- `agent/runtimes/githi/loop.py` → AgentLoop calls gateway.complete()

---

## Provider 3: PromptAssembler

### Interface (`prompt_assembler.py`)

```python
class PromptAssembler(Protocol):
    def assemble(self, bindings: PromptBindings) -> AssembledPrompt: ...
```

**Keep sync** - This is CPU-bound string concatenation.

### Implementations

| Provider | File | Description |
|----------|------|-------------|
| StaticPromptAssembler | `providers/static_prompt.py` | Returns fixed prompt |

---

## Provider 4: SubagentTransport

### Interface (`subagent_transport.py`)

```python
class SubagentTransport(Protocol):
    async def start_one_shot(self, provider, request) -> SubagentRunResult: ...
    async def start_fork(self, provider, request) -> ChildHandle: ...
    async def start_background(self, provider, request) -> JobHandle: ...
    # ... more methods
```

Already mostly async.

### Implementations

| Provider | File | Description |
|----------|------|-------------|
| LabEchoSubagentProvider | `providers/memory_subagent.py` | Testing/local |
| AcpSubagentProvider | `providers/acp_subagent.py` | ACP protocol |
| NestedSessionSubagentProvider | `providers/nested_session_subagent.py` | Nested sessions |

---

## Provider 5: ContextMemoryProvider

### Interface (`context_memory.py`)

```python
# CURRENT (sync)
class ContextMemoryProvider(Protocol):
    def estimate_tokens(self, messages) -> int: ...
    def before_compact(self, ctx, preparation, *, reason) -> CompactionDecision: ...
    def recall_for_turn(self, ctx, *, user_prompt) -> RecallBlock | None: ...

# TARGET (async for I/O methods)
class ContextMemoryProvider(Protocol):
    def estimate_tokens(self, messages) -> int: ...  # Keep sync (CPU)
    async def before_compact(self, ctx, preparation, *, reason) -> CompactionDecision: ...
    async def recall_for_turn(self, ctx, *, user_prompt) -> RecallBlock | None: ...
```

### Implementations

| Provider | File | Description |
|----------|------|-------------|
| NullContextMemoryProvider | `providers/null_context_memory.py` | No-op |
| BasicCompactionProvider | `providers/basic_compaction.py` | LLM summarization |
| MarkdownRecallProvider | `providers/markdown_recall.py` | File-based recall |
| McpSemanticRecallProvider | `providers/mcp_semantic_recall.py` | MCP-based recall |
| CompositeContextMemoryProvider | `providers/composite_context_memory.py` | Combines compaction + recall |

---

## Development Order

```
1. tool_provider.py          → Make invoke() async
   └── Update: null_tools.py, mcp_tools.py, composite_tools.py
   └── Update: agent/runtimes/githi/tools/provider.py

2. model_gateway.py          → Make complete() async, add stream()
   └── Update: fake_gateway.py, http_gateway.py
   └── Update: agent/runtimes/githi/loop.py

3. context_memory.py         → Make before_compact(), recall_for_turn() async
   └── Update: null_context_memory.py, basic_compaction.py, etc.

4. factory.py                → Review construction logic

5. subagent_transport.py     → Already async, review for consistency
```

---

## Current Status

| Provider | Interface | Implementations | Status |
|----------|-----------|-----------------|--------|
| ToolProvider | `tool_provider.py` | 3 providers | Not started |
| ModelGatewayClient | `model_gateway.py` | 2 providers | Not started |
| PromptAssembler | `prompt_assembler.py` | 1 provider | Keep sync |
| SubagentTransport | `subagent_transport.py` | 3 providers | Review needed |
| ContextMemoryProvider | `context_memory.py` | 5 providers | Not started |
