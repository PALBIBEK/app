# Development Roadmap: Composable Agent Platform

This document outlines the development sequence for building a scalable, composable agent platform.

---

## Target Architecture: Composable Harness Flow

### The 7-Step Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                    │
│                  User selects: tools, prompts, sandbox, model           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1: CATALOG                                                         │
│  Stores reusable components (PromptPack, ToolSet, SandboxProfile)       │
│  User browses and picks components on frontend                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 2: COMPOSITION                                                     │
│  User's selections → AgentComposition (digest-addressed)                │
│  Immutable bundle: prompt + tools + sandbox + memory config             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 3: SESSION CREATION                                                │
│  SessionManager.create(composition_id)                                  │
│  Resolves composition → SessionPins (immutable snapshot)                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 4: RUNTIME SEAMS                                                   │
│  Build RuntimeSeams from SessionPins:                                   │
│  - ToolProvider (selected tools)                                        │
│  - PromptAssembler (selected prompts)                                   │
│  - ModelGateway (selected model)                                        │
│  - SubagentTransport (if subagents enabled)                             │
│  - ContextMemory (compaction/recall settings)                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 5: AGENT RUNTIME                                                   │
│  GithiRuntime initialized with seams                                    │
│  AgentLoop executes: LLM calls → tool execution → events                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 6: JOURNAL & POLICY                                                │
│  - Journal persists all events (durability)                             │
│  - Policy enforces budgets, approvals, guards                           │
│  - Enables crash recovery and audit                                     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 7: TEMPORAL (Optional)                                             │
│  For long-running sessions that survive:                                │
│  - Process restarts                                                     │
│  - Deployments                                                          │
│  - Infrastructure failures                                              │
│  - Indefinite human approval waits                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Dependency Graph

```
                         ┌─────────┐
                         │   API   │  ← Last (exposes everything)
                         └────┬────┘
                              │
                         ┌────▼────┐
                         │ Session │  ← Orchestrates all below
                         └────┬────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
    ┌────▼────┐         ┌────▼────┐         ┌────▼────┐
    │ Catalog │         │ Policy  │         │ Sandbox │
    └────┬────┘         └────┬────┘         └────┬────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                         ┌────▼────┐
                         │ Journal │  ← Event persistence
                         └────┬────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
    ┌────▼────┐         ┌────▼────┐         ┌────▼────┐
    │  Seams  │         │   DB    │         │ Harness │
    └─────────┘         └─────────┘         └─────────┘
         │
    ┌────▼────┐
    │  Agent  │  ← Already improved
    └─────────┘
```

---

## Development Phases

### Phase 1: `seams/` (Foundation)

**Location:** `supervisor/src/supervisor/seams/`

**Why first:** Everything depends on these interfaces. Making them async eliminates the sync→async bridge problem.

**Key changes:**
```python
# BEFORE (sync)
class ToolProvider(Protocol):
    def invoke(self, ctx, name, args) -> ToolResult: ...

class ModelGatewayClient(Protocol):
    def complete(self, messages, tools, *, model) -> Completion: ...

# AFTER (async)
class ToolProvider(Protocol):
    async def invoke(self, ctx, name, args) -> ToolResult: ...

class ModelGatewayClient(Protocol):
    async def complete(self, messages, tools, *, model) -> Completion: ...
    async def stream(self, messages, tools, *, model) -> AsyncIterator[StreamEvent]: ...
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `tool_provider.py` | Tool execution interface | Make async |
| `model_gateway.py` | LLM API interface | Make async |
| `subagent_transport.py` | Child agent spawning | Review async patterns |
| `context_memory.py` | Compaction/recall | Review async |
| `prompt_assembler.py` | System prompt building | Keep sync (CPU-bound) |
| `types.py` | DTOs | Keep clean |
| `factory.py` | Seam builder | Update for async |

---

### Phase 2: `db/` (Connection Pooling)

**Location:** `supervisor/src/supervisor/db/`

**Why second:** Core infrastructure that all persistence depends on.

**Key changes:**
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,           # Concurrent connections for 100 users
    max_overflow=10,        # Extra connections under load
    pool_pre_ping=True,     # Check connection health
    pool_recycle=3600,      # Recycle stale connections
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `models.py` | SQLAlchemy models | Review indexes |
| Connection config | Pool settings | Add proper pooling |

---

### Phase 3: `journal/` (Event Persistence)

**Location:** `supervisor/src/supervisor/journal/`

**Why third:** Used by session for all event persistence. Foundation for durability.

**Key changes:**
```python
# Batch inserts for efficiency
async def append_batch(self, session_id: str, events: list[AgentEvent]) -> list[int]:
    async with self._db() as db:
        rows = [EventRow(session_id=session_id, payload=serialize(e)) for e in events]
        db.add_all(rows)
        await db.commit()
        return [r.seq for r in rows]

# Efficient queries with proper indexes
# CREATE INDEX idx_events_session_seq ON events(session_id, seq);
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `writer.py` | Append events | Batch operations |
| `reader.py` | Query history | Efficient queries |
| `sync.py` | Event ordering | Review locking |
| `serde.py` | Serialization | Performance review |

---

### Phase 4: `sandbox/` (Isolation)

**Location:** `supervisor/src/supervisor/sandbox/`

**Why fourth:** Isolation layer used by session. Needs pooling for fast session starts.

**Key changes:**
```python
class SandboxPool:
    async def warm(self, count: int = 5):
        """Pre-create sandboxes for fast session starts."""
        tasks = [self._create_pooled() for _ in range(count)]
        await asyncio.gather(*tasks)

    async def acquire(self, timeout: float = 30.0) -> Sandbox:
        """Get a pre-warmed sandbox or create new."""
        try:
            return await asyncio.wait_for(self._pool.get(), timeout)
        except asyncio.TimeoutError:
            return await self._create_new()
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `base.py` | Sandbox protocol | Ensure async lifecycle |
| `factory.py` | Sandbox creation | Async factory |
| `pool/manager.py` | Pool management | Pre-warming, health checks |
| `pool/providers/` | Provider implementations | Async operations |
| `reaper.py` | Cleanup | Proper async cleanup |
| `workspace_tree.py` | File I/O | Async file operations |

---

### Phase 5: `policy/` (Guardrails)

**Location:** `supervisor/src/supervisor/policy/`

**Why fifth:** Guardrails that integrate with session. Needs async DB checks.

**Key changes:**
```python
class BudgetTracker:
    async def check(self, session_id: str) -> BudgetState:
        """Async budget check with caching."""
        cached = self._cache.get(session_id)
        if cached and not cached.stale:
            return cached.state
        async with self._db() as db:
            state = await self._load_state(db, session_id)
        self._cache[session_id] = CachedState(state, time.time())
        return state
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `budget.py` | Token/cost limits | Async + caching |
| `quotas.py` | Tenant limits | Async checks |
| `approvals.py` | Human-in-loop | Already async, review |
| `guards.py` | Loop detection | Stateless, review logic |
| `rate_limit.py` | Throttling | Review implementation |

---

### Phase 6: `catalog/` (Component Store)

**Location:** `supervisor/src/supervisor/catalog/`

**Why sixth:** Component store for compositions. Needs caching for frequent access.

**Key changes:**
```python
class CachedCatalog:
    def __init__(self, store: CompositionCatalog, ttl: int = 300):
        self._store = store
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl

    async def resolve(self, composition_id: str) -> AgentComposition:
        now = time.time()
        if composition_id in self._cache:
            ts, value = self._cache[composition_id]
            if now - ts < self._ttl:
                return value
        value = await self._store.get(composition_id)
        self._cache[composition_id] = (now, value)
        return value
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `types.py` | DTOs | Keep clean |
| `store.py` | Persistence | Async + caching |
| `resolve.py` | Lookup | Async resolution |
| `binder.py` | Build seams | Async binding |
| `digests.py` | Content hashing | Keep sync (CPU) |
| `verify.py` | Validation | Keep sync |

---

### Phase 7: `harness/` (Configuration)

**Location:** `supervisor/src/supervisor/harness/`

**Why seventh:** Configuration layer. Clean validation and manifest handling.

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `manifest.py` | HarnessManifest | Clean dataclasses |
| `resolver.py` | Version lookup | Efficient resolution |
| `compat.py` | Capability checks | Clean logic |
| `bind_files.py` | Materialize to disk | Review I/O |
| `validate.py` | Validation | Comprehensive checks |

---

### Phase 8: `agent/` (Update for Async Seams)

**Location:** `supervisor/src/supervisor/agent/`

**Why eighth:** Already improved. Update based on seam changes.

**Key changes:**
```python
# BEFORE (in loop.py) - sync bridge
tool_result = await asyncio.to_thread(provider.invoke, ctx, name, args)
completion = await asyncio.to_thread(gateway.complete, chat, tools)

# AFTER - direct async (no bridge needed)
tool_result = await provider.invoke(ctx, name, args)
completion = await gateway.complete(chat, tools)
```

**Remove `_run_async` bridge entirely from `tools/provider.py`.**

---

### Phase 9: `session/` (Orchestration)

**Location:** `supervisor/src/supervisor/session/`

**Why ninth:** The big orchestrator. Integrates all async improvements.

**Key changes:**
- Use async seams directly
- Proper connection pool usage
- Batch journal writes where possible
- Efficient sandbox acquisition from pool

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `manager.py` | Main orchestrator | Integrate all async |
| `lifecycle.py` | State transitions | Clean state machine |
| `recovery.py` | Cold resume | Reliable recovery |
| `registry.py` | Session tracking | Thread-safe |
| `workspaces.py` | Workspace management | Async file ops |

---

### Phase 10: `api/` (External Interface)

**Location:** `supervisor/src/supervisor/api/`

**Why tenth:** External interface. Exposes all functionality.

**Key changes:**
```python
from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: warm pools
    await sandbox_pool.warm(count=10)
    await catalog_cache.preload()
    yield
    # Shutdown: drain sessions
    await session_manager.drain(timeout_seconds=30)

app = FastAPI(lifespan=lifespan)

@app.post("/sessions")
async def create_session(req: CreateSessionRequest):
    # All async, no blocking
    session_id = await session_manager.create(
        tenant_id=req.tenant_id,
        composition_id=req.composition_id,
    )
    return {"session_id": session_id}
```

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `sessions.py` | Session endpoints | Async handlers |
| `tools.py` | Tool invocation | Async handlers |
| `approvals.py` | Approval flow | Async handlers |
| `events.py` | SSE streaming | Proper async streaming |
| `deps.py` | Dependencies | Connection pooling |
| `health.py` | Health checks | Pool health |

---

### Phase 11: `temporal/` (Optional Durability)

**Location:** `supervisor/src/supervisor/temporal/`

**Why last:** Optional. For sessions that need to survive crashes.

**Files to review:**
| File | Purpose | Changes Needed |
|------|---------|----------------|
| `workflows/session.py` | Session workflow | Review state management |
| `workflows/turn.py` | Turn workflow | Review checkpointing |
| `activities/` | Activity implementations | Async activities |
| `client.py` | Temporal client | Connection management |

---

## Development Workflow Per Phase

```
For each phase:

1. READ & UNDERSTAND
   ├── Read all files in the folder
   ├── Understand current patterns
   ├── Identify dependencies on other folders
   └── Note issues and improvements

2. DOCUMENT
   ├── Create ARCHITECTURE.md for the folder
   ├── Document interfaces and flows
   └── Note design decisions

3. IMPROVE CODE QUALITY
   ├── Fix typing issues
   ├── Remove dead code
   ├── Improve naming
   ├── Add missing error handling
   └── Ensure consistent patterns

4. MAKE ASYNC/SCALE CHANGES
   ├── Convert sync to async where needed
   ├── Add connection pooling
   ├── Add caching
   ├── Implement batch operations
   └── Add proper timeouts

5. TEST
   ├── Review existing tests
   ├── Add tests for new patterns
   ├── Test concurrent access
   └── Test error scenarios
```

---

## Scalability Targets

| Metric | Target | How |
|--------|--------|-----|
| Concurrent users | 100+ | Single event loop, async I/O |
| Session start time | < 500ms | Sandbox pooling, cached catalog |
| Tool execution | Non-blocking | Async ToolProvider |
| LLM calls | Non-blocking | Async ModelGateway |
| DB connections | Pooled (20+10) | SQLAlchemy async pool |
| Memory per session | Minimal | Stream events, don't buffer |

---

## Quick Reference: File Locations

```
services/supervisor/src/supervisor/
├── seams/           # Phase 1: DI interfaces
├── db/              # Phase 2: Database
├── journal/         # Phase 3: Event persistence
├── sandbox/         # Phase 4: Isolation
├── policy/          # Phase 5: Guardrails
├── catalog/         # Phase 6: Component store
├── harness/         # Phase 7: Configuration
├── agent/           # Phase 8: Runtimes (done)
├── session/         # Phase 9: Orchestration
├── api/             # Phase 10: HTTP endpoints
└── temporal/        # Phase 11: Durable workflows
```

---

## Current Status

| Phase | Folder | Status |
|-------|--------|--------|
| 1 | `seams/` | **Completed** ✓ |
| 2 | `db/` | **Completed** ✓ |
| 3 | `journal/` | **Completed** ✓ |
| 4 | `sandbox/` | **Next** |
| 5 | `policy/` | Not started |
| 6 | `catalog/` | Not started |
| 7 | `harness/` | Not started |
| 8 | `agent/` | **Completed** ✓ |
| 9 | `session/` | Not started |
| 10 | `api/` | Not started |
| 11 | `temporal/` | Not started |

---

## Completed Work

### Phase 1: seams/ ✓
- Restructured from flat files to organized subfolders
- Created: `interfaces/`, `core/`, `providers/`, `services/`
- Added SEAMS_ARCHITECTURE.md and SERVICES_ARCHITECTURE.md
- Clean separation: Protocols → DTOs/Errors → Implementations → Service abstractions

### Phase 2: db/ ✓
- Production-ready connection pooling (pool_size=20, max_overflow=10, pool_recycle=3600)
- Environment-based configuration (DATABASE_URL, DB_POOL_SIZE, etc.)
- Health check and pool status utilities
- Removed dead repositories.py stub
- Added DB_ARCHITECTURE.md

### Phase 3: journal/ ✓
- Removed dead stubs (store.py, sync.py)
- Improved serde.py with typed errors (SerializationError, DeserializationError)
- Added helper functions (is_known_event_type, registered_event_types)
- Clean exports in __init__.py
- Added JOURNAL_ARCHITECTURE.md

---

## Next Step

**Continue with Phase 4: `sandbox/`**

Isolation layer: sandbox pooling, lifecycle management, workspace I/O.
