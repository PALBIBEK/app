# QM Architecture Study

**Purpose**: Extract production patterns from YC's QM open-source harness to properly implement Githi's migration to microservices architecture.

**Study Date**: 2026-08-31
**QM Repository**: `/mnt/c/Users/91790/Desktop/Corridor Platforms/qm`
**Studied By**: Architecture Research Analysis

---

## Executive Summary

QM is YC's production-grade, multiplayer agent harness built with TypeScript/Node.js. It demonstrates enterprise patterns for:
- Multi-tenant scope isolation (personal/channel/team/org scopes)
- Durable-by-default PostgreSQL persistence
- Pluggable sandbox backends (Docker, Firecracker, cloud)
- Lease-based concurrency control for turn serialization
- Complete audit trails and event journaling

**Key Insight**: QM's architecture solves the exact problems we're facing in Githi migration - message persistence, sandbox isolation, multi-user support, and proper agent loop integration.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Headless Core                      │
│  ┌────────────┐     ┌──────────────────────┐       │
│  │ API        │<--->│  Orchestrator        │       │
│  │ Identity   │     │  (Agent Loop Wiring) │       │
│  │ Policy     │     └──────────────────────┘       │
│  │ Scheduler  │              ↓↑                     │
│  └────────────┘         ┌──────────┐                │
│       ↑↓                │ Harness  │                │
│  ┌──────────┐          │ (Pi,     │                │
│  │PostgreSQL│          │ OpenCode,│                │
│  │sessions  │          │ Claude   │                │
│  │memory    │          │  Code)   │                │
│  │queue     │          └──────────┘                │
│  └──────────┘               ↓↑                      │
└────────────────────────────┼┼──────────────────────┘
                             ↓↑
                    ┌─────────────────┐
                    │ Per-Scope       │
                    │ Sandbox         │
                    │ (files, tools,  │
                    │  credentials)   │
                    └─────────────────┘
```

### Core Components

1. **Orchestrator** (`src/core/orchestrator.ts`) - Wires agent loop to runtime
2. **SessionStore** (`src/sessions/session-store.ts`) - PostgreSQL persistence with leases
3. **Sandbox** (`src/sandbox/sandbox.ts`) - Pluggable execution backends
4. **Harness** (`src/harness/harness.ts`) - Agent loop interfaces (Pi, OpenCode, Claude Code)
5. **Scope Resolution** - Multi-tenant isolation layer

---

## Core Abstractions

### 1. SessionStore Interface

**Purpose**: Durable message history and conversation state

**Key Methods**:
```typescript
interface SessionStore {
  // Session lifecycle
  create(session: Omit<Session, "id">): Promise<Session>
  get(sessionId: string): Promise<Session | null>
  update(sessionId: string, patch: SessionPatch): Promise<void>

  // Message history
  appendEntries(sessionId: string, entries: NewEntry[]): Promise<void>
  getEntries(sessionId: string, options?: GetEntriesOptions): Promise<SessionEntry[]>

  // Lease-based concurrency (CRITICAL PATTERN)
  acquireLease(sessionId: string, holder: LeaseHolder, ttlMs: number): Promise<LeaseAttempt>
  releaseLease(lease: Lease): Promise<void>

  // Tape (event journal for replay)
  appendTape(sessionId: string, records: NewTapeRecord[]): Promise<void>
  getTape(sessionId: string, options?: GetTapeOptions): Promise<TapeRecord[]>
}
```

**Critical Pattern - Lease-Based Concurrency**:
```typescript
// Serialize concurrent turn processing
const leaseAttempt = await sessionStore.acquireLease(
  sessionId,
  "turn",      // holder type
  60_000       // TTL in ms
);

if (!leaseAttempt.lease) {
  // Another process is handling this session
  throw new Error(`Session locked by ${leaseAttempt.heldBy}`);
}

try {
  // Process turn with exclusive access
  await processTurn(sessionId);
} finally {
  // ALWAYS release lease
  await sessionStore.releaseLease(leaseAttempt.lease);
}
```

### 2. Sandbox Interface

**Purpose**: Isolated execution environment for tool execution

**Key Methods**:
```typescript
interface Sandbox {
  // Provision sandbox with layered filesystem
  provision(
    layers: SandboxLayer[],
    options: ProvisionOptions
  ): Promise<SandboxHandle>

  // Execute command in sandbox
  exec(
    handle: SandboxHandle,
    command: string,
    options: ExecOptions
  ): Promise<ExecResult>

  // File operations
  readFile(handle: SandboxHandle, path: string): Promise<string>
  writeFile(handle: SandboxHandle, path: string, content: string): Promise<void>
  listDir(handle: SandboxHandle, path: string): Promise<string[]>
  removeDir(handle: SandboxHandle, path: string): Promise<void>

  // Lifecycle
  teardown(handle: SandboxHandle, options?: TeardownOptions): Promise<void>
}
```

**Sandbox Layers** (Read-Only Base + Read-Write Overlay):
```typescript
interface SandboxLayer {
  scopeId: ScopeId;        // Which scope owns this layer
  mountPath: string;       // Where to mount ("" = root, "global" = /global)
  mode: "ro" | "rw";      // Read-only or read-write
}

// Example: Org base (RO) + User workspace (RW)
const layers: SandboxLayer[] = [
  { scopeId: "org:acme", mountPath: "global", mode: "ro" },   // Org resources
  { scopeId: "user:alice", mountPath: "", mode: "rw" }        // User workspace
];
```

### 3. Orchestrator Pattern

**Purpose**: Wire agent loop to runtime, managing sandbox lifecycle and tool execution

**Key Structure** (`src/core/orchestrator.ts`):
```typescript
export function createOrchestrator(deps: OrchestratorDeps): Orchestrator {
  return {
    async turn(input: OrchestratorInput): Promise<TurnResult> {
      // 1. Acquire lease
      const lease = await deps.sessionStore.acquireLease(input.sessionId, "turn", 60_000);
      if (!lease.lease) throw new Error("Session locked");

      try {
        // 2. Load session and message history
        const session = await deps.sessionStore.get(input.sessionId);
        const entries = await deps.sessionStore.getEntries(input.sessionId);

        // 3. Provision sandbox with layers
        const sandboxes = createTurnSandboxes({
          deps,
          session,
          scopeId: session.scopeId,
          // ... context
        });
        const handle = await sandboxes.provision();

        try {
          // 4. Build context for harness
          const context = await buildHarnessContext({
            session,
            entries,
            sandbox: handle,
            // ... tools, memory, etc
          });

          // 5. Call harness (agent loop)
          const result = await deps.harness.turn({
            sessionId: input.sessionId,
            message: input.message,
            context,
            tools: createToolContext(handle, deps),
            onChunk: async (chunk) => {
              // Stream to user
              await deps.eventSink.emit(chunk);
            }
          });

          // 6. Persist results
          await deps.sessionStore.appendEntries(input.sessionId, result.entries);
          await deps.sessionStore.appendTape(input.sessionId, result.tape);

          return result;

        } finally {
          // 7. Reclaim sandbox
          await sandboxes.reclaimBox();
        }
      } finally {
        // 8. Release lease
        await deps.sessionStore.releaseLease(lease.lease);
      }
    }
  };
}
```

### 4. Scope Model

**Purpose**: Multi-tenant isolation (personal, channel, team, org)

**Scope Types**:
```typescript
type ScopeId =
  | `user:${string}`        // Personal workspace
  | `channel:${string}`     // Slack channel
  | `team:${string}`        // Team shared scope
  | `org:${string}`         // Organization-wide
  | `group:${string}`;      // Custom group

interface Resolution {
  scopeId: ScopeId;          // Current scope
  orgScopeId: ScopeId;       // Org scope for base layer
  memoryScopeId: ScopeId;    // Where to store memories
  layers: SandboxLayer[];    // Filesystem layers
  egress: EgressPolicy;      // Network access policy
}
```

**Isolation Guarantees**:
- Each scope has isolated filesystem in sandbox
- Each scope has isolated memory/embeddings
- Each scope has isolated credentials (keychain)
- Control plane enforces scope boundaries

---

## PostgreSQL Schema Patterns

### Sessions Table
```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,                    -- "conversation", "cron", "watch"
  scope_id TEXT NOT NULL,                -- Scope ownership
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  last_activity BIGINT NOT NULL,
  participant_ids TEXT[] NOT NULL,       -- Who's in this conversation
  title TEXT,
  archived BOOLEAN DEFAULT FALSE,
  -- Metadata as JSONB
  metadata JSONB
);

CREATE INDEX idx_sessions_scope_activity
  ON sessions(scope_id, last_activity DESC);
```

### Session Entries Table (Message History)
```sql
CREATE TABLE session_entries (
  session_id TEXT NOT NULL REFERENCES sessions(id),
  seq BIGSERIAL NOT NULL,                -- Monotonic sequence per session
  type TEXT NOT NULL,                    -- "user_message", "assistant_message", "tool_call", etc
  created_at BIGINT NOT NULL,
  scope_label TEXT NOT NULL,             -- Scope that created this entry
  payload JSONB NOT NULL,                -- Entry data

  PRIMARY KEY (session_id, seq)
);

CREATE INDEX idx_entries_session_seq
  ON session_entries(session_id, seq);
```

### Session Leases Table (Concurrency Control)
```sql
CREATE TABLE session_leases (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id),
  token TEXT NOT NULL,                   -- Random UUID
  holder TEXT NOT NULL,                  -- "turn", "compaction", "fork"
  acquired_at BIGINT NOT NULL,
  expires_at BIGINT NOT NULL
);

-- Cleanup expired leases periodically
DELETE FROM session_leases WHERE expires_at < extract(epoch from now()) * 1000;
```

### Session Tape Table (Event Journal for Replay)
```sql
CREATE TABLE session_tape (
  session_id TEXT NOT NULL REFERENCES sessions(id),
  seq BIGSERIAL NOT NULL,
  kind TEXT NOT NULL,                    -- "message", "context_event", "annotation"
  payload JSONB NOT NULL,
  scope_label TEXT NOT NULL,
  harness TEXT,                          -- Which harness generated this
  entry_seq BIGINT,                      -- Link to session_entries
  created_at BIGINT NOT NULL,

  PRIMARY KEY (session_id, seq)
);
```

### LLM Requests Table (Model Call Tracking)
```sql
CREATE TABLE llm_requests (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  turn_seq BIGINT,                       -- Which turn
  step INTEGER NOT NULL,                 -- Which step in turn
  model TEXT NOT NULL,
  scope_label TEXT NOT NULL,
  created_at BIGINT NOT NULL,

  -- Request/response data
  request JSONB NOT NULL,
  prompt_hash TEXT,                      -- For dedup
  truncated BOOLEAN DEFAULT FALSE,

  -- Performance metrics
  ttft_ms INTEGER,                       -- Time to first token
  duration_ms INTEGER,                   -- Total duration
  tool_wall_ms INTEGER[],                -- Tool execution times
  gap_phases JSONB,                      -- Performance breakdown

  -- Usage and cost
  usage JSONB,                           -- Token counts
  transport JSONB                        -- Model-specific metadata
);

CREATE INDEX idx_llm_requests_session
  ON llm_requests(session_id, turn_seq, step);
```

---

## Agent Loop Integration Pattern

### How QM Wires Agent Loop to Runtime

**File**: `src/core/orchestrator.ts` (lines 196-2000+)

**Pattern**:
1. **Orchestrator creates harness context**
2. **Calls harness.turn() with tools and sandbox**
3. **Harness streams events back**
4. **Orchestrator persists results to PostgreSQL**

**Simplified Flow**:
```typescript
// 1. Orchestrator prepares context
const context = {
  messages: await buildMessageHistory(entries),
  systemPrompt: await buildSystemPrompt({
    scopeId,
    skills: visibleSkills,
    memory: await recallMemories(),
    computer: await renderComputerBlock(sandbox),
  }),
  tools: toolDefinitions,
};

// 2. Call harness (agent loop)
const result = await harness.turn({
  sessionId,
  message: userMessage,
  context,
  tools: {
    async execute(toolName: string, args: unknown) {
      // Execute in sandbox
      const toolResult = await executeTool(sandbox, toolName, args);
      // Audit log
      await deps.auditLog.record({
        action: "tool.execute",
        resource: toolName,
        scopeLabel: scopeId,
      });
      return toolResult;
    }
  },
  onChunk: async (chunk) => {
    // Stream to user in real-time
    await deps.eventSink.emit({ type: "chunk", payload: chunk });
  }
});

// 3. Persist results
await deps.sessionStore.appendEntries(sessionId, result.newEntries);
await deps.sessionStore.update(sessionId, { lastActivity: Date.now() });
```

---

## Security Model

### Authorization Boundaries

**Scope-Based Isolation**:
- Each scope has isolated filesystem (sandbox layers)
- Each scope has isolated database records (scope_id foreign key)
- Each scope has isolated credentials (keychain entries)
- Each scope has isolated memory (embeddings scoped by scope_id)

**Permission Model**:
```typescript
interface Grant {
  granteeId: ScopeId;      // Who receives access
  resourceRef: string;      // What they access (e.g., "skill:deploy-qm")
  grantedBy: ScopeId;      // Who granted it
  createdAt: number;
}

// Example: Grant channel access to org skill
await grants.create({
  granteeId: "channel:engineering",
  resourceRef: "skill:deploy-qm@org:acme",
  grantedBy: "user:admin"
});
```

### Command Policy Evaluation

**Purpose**: Deny dangerous commands before execution

**Pattern** (`src/policy/command-policy.ts`):
```typescript
interface CommandPolicy {
  // Hard denials
  deny: RegExp[];          // Never allow these patterns

  // Approval required
  require_approval: RegExp[];
}

// Example policy
const policy: CommandPolicy = {
  deny: [
    /rm\s+-rf\s+\//,                    // Recursive delete from root
    /DROP\s+DATABASE/i,                  // Destructive SQL
    /:\(\)\{ :\|:& \};:/,                // Fork bomb
  ],
  require_approval: [
    /git\s+push\s+--force/,              // Force push
    /docker\s+system\s+prune/,           // Docker cleanup
  ]
};

// Evaluate before execution
const evaluation = evaluateCommandWithLayer(command, policy);
if (evaluation.denied) {
  throw new CommandDenied(evaluation.reason);
}
if (evaluation.needsApproval) {
  await requestHumanApproval(evaluation.reason);
}
```

### Audit Logging

**Every security decision is logged**:
```typescript
interface AuditRecord {
  at: number;                  // Timestamp
  principalId: string;         // Who did it
  action: string;              // What they did
  resource: string;            // What they accessed
  scopeLabel: ScopeId;         // Which scope
  metadata?: unknown;          // Additional context
}

// Examples
auditLog.record({
  at: Date.now(),
  principalId: "user:alice",
  action: "keychain.materialize",
  resource: "github",
  scopeLabel: "user:alice"
});

auditLog.record({
  at: Date.now(),
  principalId: "user:bob",
  action: "tool.execute",
  resource: "bash",
  scopeLabel: "channel:engineering",
  metadata: { command: "git push" }
});
```

---

## Workspace Layers Pattern

### Layered Filesystem Mounting

**Concept**: Mount multiple scopes into single sandbox
- **Base layer (RO)**: Org-wide resources (skills, tools, docs)
- **Overlay layer (RW)**: User/channel workspace (files, state)

**Implementation** (`src/core/orchestrator/sandboxes.ts:187-196`):
```typescript
const handle = await deps.sandbox.provision(resolution.layers, {
  env: connectorEnv,
  egress: resolution.egress,
});

// Layers defined in resolution
const layers: SandboxLayer[] = [
  { scopeId: "org:acme", mountPath: "global", mode: "ro" },
  { scopeId: "user:alice", mountPath: "", mode: "rw" }
];

// Results in sandbox filesystem:
// /global/               (org resources, read-only)
//   skills/
//     deploy-qm/
//   tools/
// /workspace/            (user workspace, read-write)
//   my-project/
//   .bash_history
```

**Benefits**:
- Share org resources without duplication
- Isolate user data
- Clear permission boundaries (can't modify org resources)
- Support for nested scopes (team → org)

---

## Critical Patterns to Apply to Githi

### 1. Implement SessionStore with PostgreSQL

**Why**: Fix Critical Gap #2 (ephemeral message history)

**Action**:
```python
class PostgresSessionStore:
    """SessionStore backed by PostgreSQL"""

    async def create_session(self, session: Session) -> Session:
        # INSERT INTO sessions
        pass

    async def append_entries(self, session_id: str, entries: list[Entry]) -> None:
        # INSERT INTO session_entries with seq = nextval('seq_...')
        pass

    async def get_entries(self, session_id: str, since_seq: int = 0) -> list[Entry]:
        # SELECT * FROM session_entries WHERE session_id = $1 AND seq > $2
        pass

    async def acquire_lease(
        self,
        session_id: str,
        holder: str,
        ttl_ms: int
    ) -> LeaseAttempt:
        # INSERT INTO session_leases ON CONFLICT DO NOTHING
        # Check if we got the lease or someone else has it
        pass
```

### 2. Implement Sandbox Interface

**Why**: Fix Critical Gap #3 (no isolation), prepare for Phase 2

**Action** (start simple, iterate):
```python
class SubprocessSandbox:
    """Simple subprocess-based sandbox (Phase 1)"""

    async def provision(self, layers: list[Layer], options: dict) -> SandboxHandle:
        # For now: just create temp directory
        # Later: Docker container, Firecracker VM
        temp_dir = tempfile.mkdtemp(prefix="githi-sandbox-")
        return SandboxHandle(root_dir=temp_dir, env={})

    async def exec(self, handle: SandboxHandle, command: str) -> ExecResult:
        # Run command in subprocess with cwd=handle.root_dir
        result = await asyncio.create_subprocess_shell(
            command,
            cwd=handle.root_dir,
            env=handle.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()
        return ExecResult(stdout=stdout, stderr=stderr, returncode=result.returncode)
```

### 3. Implement Orchestrator Pattern

**Why**: Fix Critical Gap #1 (agent loop not integrated)

**Action**:
```python
class GithiOrchestrator:
    """Wire agent loop to runtime following QM pattern"""

    def __init__(self, deps: OrchestratorDeps):
        self.session_store = deps.session_store
        self.sandbox = deps.sandbox
        self.llm_provider = deps.llm_provider
        self.tool_provider = deps.tool_provider

    async def run_turn(
        self,
        session_id: str,
        message: Message
    ) -> AsyncIterator[Event]:
        # 1. Acquire lease
        lease_attempt = await self.session_store.acquire_lease(
            session_id, "turn", 60_000
        )
        if not lease_attempt.lease:
            raise RuntimeError(f"Session locked by {lease_attempt.held_by}")

        try:
            # 2. Load session and entries
            session = await self.session_store.get_session(session_id)
            entries = await self.session_store.get_entries(session_id)

            # 3. Provision sandbox
            handle = await self.sandbox.provision(
                layers=[{"scope_id": session.scope_id, "mount_path": "", "mode": "rw"}],
                options={}
            )

            try:
                # 4. Create agent loop
                loop = AgentLoop(
                    llm_provider=self.llm_provider,
                    tool_provider=self.tool_provider,
                    sandbox=handle,
                    message_history=self._build_history(entries)
                )

                # 5. Run turn and stream events
                new_entries = []
                async for event in loop.run_turn(message):
                    yield event
                    if event.type == "message_end":
                        new_entries.append(event.payload)

                # 6. Persist results
                await self.session_store.append_entries(session_id, new_entries)

            finally:
                # 7. Teardown sandbox
                await self.sandbox.teardown(handle)

        finally:
            # 8. Release lease
            await self.session_store.release_lease(lease_attempt.lease)
```

### 4. Add Scope Model

**Why**: Enable multi-user support, prepare for Phase 3

**Action**:
```python
@dataclass(frozen=True)
class ScopeId:
    """Scope identifier for multi-tenant isolation"""
    type: Literal["user", "org", "team"]
    id: str

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"

    @classmethod
    def parse(cls, s: str) -> ScopeId:
        type_, id_ = s.split(":", 1)
        return cls(type=type_, id=id_)

@dataclass(frozen=True)
class Resolution:
    """Scope resolution with filesystem layers"""
    scope_id: ScopeId
    org_scope_id: ScopeId
    layers: list[SandboxLayer]
```

### 5. Implement Event Journaling (Tape)

**Why**: Enable crash recovery, replay, debugging

**Action**:
```python
async def append_tape(self, session_id: str, records: list[TapeRecord]) -> None:
    """Append events to session tape for replay"""
    # INSERT INTO session_tape (session_id, kind, payload, ...)
    # Used for:
    # - Debugging: replay turn to see what happened
    # - Recovery: detect crashed turns (orphaned tool_start events)
    # - Audit: complete history of all events
    pass
```

---

## Implementation Timeline

### Week 1 (IMMEDIATE)

**Critical Gaps Fix**:
1. ✅ Study QM architecture (DONE - this document)
2. **Implement SessionStore** with PostgreSQL
   - Create schema migrations
   - Implement PostgresSessionStore class
   - Add lease acquisition/release
   - Wire into RuntimeSeams
3. **Implement Orchestrator pattern**
   - Wire AgentLoop into runtime._process_one_message
   - Add sandbox provisioning
   - Add lease management
   - Test end-to-end flow
4. **Implement simple Sandbox**
   - Start with SubprocessSandbox (temp dir + subprocess)
   - Implement provision/exec/teardown
   - Add to RuntimeSeams

### Month 2-3 (PRODUCTION HARDENING)

**Full Feature Parity**:
1. **Upgrade Sandbox to Docker**
   - Replace subprocess with Docker containers
   - Implement layered filesystem (volumes)
   - Add network isolation
2. **Add Scope Model**
   - Multi-tenant support
   - Scope-based isolation
   - Permission boundaries
3. **Add Event Journaling**
   - session_tape table
   - Replay capability
   - Crash detection
4. **Add Audit Logging**
   - Every security decision logged
   - Tool execution tracking
   - Credential materialization tracking

### Long-term (SCALE)

**Enterprise Features**:
1. **Upgrade to Firecracker/gVisor**
   - VM-level isolation
   - Better security boundaries
2. **Add Command Policy**
   - Deny dangerous commands
   - Require approval for risky operations
3. **Add Memory System**
   - Vector embeddings per scope
   - Memory recall during turns
4. **Add Skills System**
   - Scope-owned skills
   - Skill materialization
   - Grant-based sharing

---

## Key Learnings

### 1. Durable by Default

**QM Principle** (from `AGENTS.md`/`CLAUDE.md`):
> "A recurring mistake: stashing state the system later relies on in process memory. Anything an operator or the system reads back later (audit, logs, resolved config, queued or in-flight work) must live in a durable store, never RAM alone."

**Application to Githi**:
- ❌ Don't: Store message history in AgentLoop._messages (ephemeral)
- ✅ Do: Store in PostgreSQL session_entries table
- ❌ Don't: Track active sessions in dict (lost on restart)
- ✅ Do: Track in PostgreSQL sessions table with leases

### 2. Lease Pattern for Concurrency

**Problem**: Multiple processes trying to handle same session concurrently
**Solution**: Lease-based exclusive access with TTL

**Why TTL Matters**: If process crashes while holding lease, TTL ensures lease expires and session becomes available again

### 3. Sandbox Lifecycle Management

**Pattern**: Provision → Use → Teardown (always in finally block)
- Provision can be slow (Docker pull, Firecracker boot)
- Use eagerly provision to parallelize with other work
- Always teardown, even on error

### 4. Scope Isolation is Fundamental

**QM Architecture**: Every resource is scoped
- Sessions: `scope_id` column
- Entries: `scope_label` column
- Sandboxes: Layered filesystem per scope
- Memory: Embeddings scoped by `scope_id`
- Credentials: Keychain entries per scope owner

**Benefit**: Clean multi-tenant isolation without complex ACLs

### 5. Event Journaling Enables Recovery

**Tape Pattern**: Record every event (not just state changes)
- Tool start/end (detect crashes)
- LLM calls (replay, cost tracking)
- Annotations (context for debugging)

**Use Cases**:
- Detect orphaned tool calls (tool_start without tool_end = crash)
- Replay turn for debugging
- Generate audit reports
- Calculate costs

---

## Code Examples from QM

### Lease Acquisition Pattern

**File**: `src/sessions/session-store.ts` (interface)

```typescript
interface LeaseAttempt {
  lease: Lease | null;           // Got lease? Populated if we acquired it
  heldBy?: LeaseHolder;          // Who else holds it (if we didn't get it)
  heldSince?: number;            // When they got it
  heldUntil?: number;            // When their lease expires
}

// Usage
const attempt = await sessionStore.acquireLease(sessionId, "turn", 60_000);
if (!attempt.lease) {
  // Someone else has it
  console.log(`Locked by ${attempt.heldBy} until ${attempt.heldUntil}`);
  throw new Error("Session busy");
}
// We have exclusive access
```

### Sandbox Provisioning with Layers

**File**: `src/core/orchestrator/sandboxes.ts:187-196`

```typescript
const handle = await deps.sandbox.provision(resolution.layers, {
  env: connectorEnv,
  egress: resolution.egress,
  ...(egressTokenForTurn ? { egressToken: egressTokenForTurn } : {}),
  ...(onSandboxStatus ? { onStatus: onSandboxStatus } : {}),
});

// Add turn-specific environment
handle.env = {
  ...handle.env,
  AGENT_OUTBOX: `${handle.rootDir}/${turnOutboxDir}`
};
```

### Tool Execution with Audit

**File**: `src/core/orchestrator.ts` (conceptual from multiple sections)

```typescript
const executeTool = async (
  toolName: string,
  args: unknown,
  context: ToolContext
): Promise<unknown> => {
  // Evaluate command policy
  if (toolName === "execute") {
    const evaluation = evaluateCommandWithLayer(args.command, policy);
    if (evaluation.denied) {
      throw new CommandDenied(evaluation.reason);
    }
  }

  // Audit log
  auditLog.record({
    at: Date.now(),
    principalId: context.actorId,
    action: "tool.execute",
    resource: toolName,
    scopeLabel: context.scopeId
  });

  // Execute in sandbox
  const result = await sandbox.exec(handle, toolCommand, { timeout: 30_000 });

  return result;
};
```

---

## Recommendations for Githi Implementation

### Priority 1: Implement SessionStore (Week 1)

**File to create**: `services/supervisor/src/supervisor/agent/runtimes/githi/session_store.py`

**Schema migrations**: `services/supervisor/migrations/001_sessions.sql`

**PostgreSQL schema**:
```sql
-- Sessions
CREATE TABLE githi_sessions (
  id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL,
  model TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  last_activity BIGINT NOT NULL
);

-- Session entries (message history)
CREATE TABLE githi_session_entries (
  session_id TEXT NOT NULL REFERENCES githi_sessions(id),
  seq BIGSERIAL NOT NULL,
  type TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (session_id, seq)
);

-- Session leases (concurrency control)
CREATE TABLE githi_session_leases (
  session_id TEXT PRIMARY KEY REFERENCES githi_sessions(id),
  token TEXT NOT NULL,
  holder TEXT NOT NULL,
  acquired_at BIGINT NOT NULL,
  expires_at BIGINT NOT NULL
);
```

### Priority 2: Wire Orchestrator Pattern (Week 1)

**File to fix**: `services/supervisor/src/supervisor/agent/runtimes/githi/runtime.py:377-391`

**Replace TODO with**:
```python
async def _process_one_message(
    self, session_id: str, message: Message
) -> AsyncIterator[Event]:
    """Process a single message through agent loop."""
    # Get session state and seams
    state = self._sessions[session_id]
    seams = self._seams[session_id]

    # Acquire lease
    lease_attempt = await seams.session_store.acquire_lease(
        session_id, "turn", 60_000
    )
    if not lease_attempt.lease:
        yield self._emitter.error(
            error_type="session_locked",
            message=f"Session locked by {lease_attempt.held_by}"
        )
        return

    try:
        # Provision sandbox
        handle = await seams.sandbox.provision(...)

        try:
            # Create agent loop
            loop = AgentLoop(
                llm_provider=seams.llm_provider,
                tool_provider=GithiToolProvider(sandbox=handle, ...),
                message_history=await seams.session_store.get_entries(session_id),
                emitter=self._emitter
            )

            # Run turn and stream events
            async for event in loop.run_turn(message):
                yield event
                # Update state based on events
                if event.type == "message_end":
                    state = state.with_message_count(state.message_count + 1)

            # Update state
            self._sessions[session_id] = state

        finally:
            # Teardown sandbox
            await seams.sandbox.teardown(handle)
    finally:
        # Release lease
        await seams.session_store.release_lease(lease_attempt.lease)
```

### Priority 3: Implement Simple Sandbox (Week 1)

**File to create**: `services/supervisor/src/supervisor/agent/runtimes/githi/sandbox.py`

**Start simple** (subprocess + temp dir):
```python
class SubprocessSandbox:
    """Simple subprocess-based sandbox for Phase 1"""

    async def provision(self, layers, options):
        temp_dir = tempfile.mkdtemp(prefix="githi-sandbox-")
        return SandboxHandle(root_dir=temp_dir, env={})

    async def exec(self, handle, command, options=None):
        # Run in subprocess with working directory
        pass

    async def teardown(self, handle, options=None):
        shutil.rmtree(handle.root_dir, ignore_errors=True)
```

**Later upgrade** to DockerSandbox, then FirecrackerSandbox.

---

## Conclusion

QM provides a battle-tested blueprint for implementing Githi properly:

**Critical Patterns Extracted**:
1. ✅ **SessionStore with leases** - Solves message persistence + concurrency
2. ✅ **Orchestrator pattern** - Solves agent loop integration
3. ✅ **Sandbox interface** - Solves tool isolation
4. ✅ **Scope model** - Solves multi-tenant support
5. ✅ **Event journaling** - Solves crash recovery + audit

**Next Steps**:
1. Implement PostgreSQL SessionStore (Week 1)
2. Wire Orchestrator into runtime._process_one_message (Week 1)
3. Implement SubprocessSandbox (Week 1)
4. Test end-to-end: create session → send message → get response → persist (Week 1)
5. Iterate toward production (Months 2-3)

**Key Insight**: QM's "durable by default" principle is fundamental - everything important goes to PostgreSQL, never RAM-only. This enables:
- Crash recovery (lease TTLs auto-expire)
- Multi-instance deployment (shared PostgreSQL state)
- Audit trails (every event persisted)
- Debugging (replay from tape)

This study provides the concrete patterns needed to fix all critical gaps in Githi's migration.

---

**Study completed**: 2026-08-31
**Next action**: Begin Week 1 implementation following Priority 1-3 above
