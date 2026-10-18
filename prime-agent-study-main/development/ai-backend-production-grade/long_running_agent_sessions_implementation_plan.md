# Long-Running Agent Sessions — Production-Grade Implementation Plan

## Goal

Build a session runtime where an agent can run for minutes, hours, days, or weeks; survive worker crashes and deployments; pause for humans; resume later; retain workspace/artifacts; and scale across many workers.

## Core principle

```text
SESSION  !=  PROCESS
SESSION  !=  SANDBOX
STATE    !=  TRACE
WORKSPACE != SANDBOX
```

The session is durable. The worker process and sandbox are replaceable.

---

# Phase 0 — Core objects

Use four durable objects:

```text
Agent
Environment
Session
Run
```

### Agent

```text
agent_id
version
model
instructions
tools
policies
```

### Environment

```text
environment_id
sandbox_image
cpu
memory
network_policy
workspace_id
```

### Session

```text
session_id
user_id
agent_id
environment_id
workspace_id
status
current_run_id
```

### Run

```text
run_id
session_id
status
attempt
checkpoint_id
started_at
ended_at
```

This separation matches current production runtime patterns: durable execution, checkpoints, memory, sandboxes, streaming and observability are separate runtime capabilities. [1]

---

# Phase 1 — Session lifecycle

```text
CREATED
   |
   v
ACTIVE
   |
   +----> WAITING_FOR_USER
   |
   +----> WAITING_FOR_TOOL
   |
   +----> PAUSED
   |
   +----> FAILED
   |
   v
COMPLETED
```

A session may remain alive even when no worker is running.

A session is **not** equivalent to a running process.

---

# Phase 2 — Run lifecycle

```text
QUEUED
  |
  v
STARTING
  |
  v
RUNNING
  |
  +--> WAITING_FOR_APPROVAL
  |
  +--> WAITING_FOR_EXTERNAL_EVENT
  |
  +--> PAUSED
  |
  v
COMPLETED
```

Failures:

```text
RUNNING
   |
   v
FAILED
   |
   +--> RETRY
   +--> RESUME
   +--> TERMINATE
```

---

# Phase 3 — PostgreSQL state model

```text
sessions
--------
id
tenant_id
user_id
agent_id
agent_version
environment_id
workspace_id
status
current_run_id
created_at
updated_at
last_activity_at

runs
----
id
session_id
status
attempt
started_at
ended_at
checkpoint_id
error_code
created_at

checkpoints
-----------
id
run_id
sequence
state_blob
state_version
created_at

session_events
--------------
id
session_id
run_id
sequence
event_type
payload
created_at
```

Use PostgreSQL as the initial control-plane source of truth.

---

# Phase 4 — Event history + current state

Maintain both:

```text
Immutable event history
+
Current state snapshot
```

Events:

```text
user.message
agent.started
llm.called
tool.called
tool.completed
sandbox.started
artifact.created
human.approval_requested
human.approval_received
agent.paused
agent.resumed
agent.completed
```

Current state:

```json
{
  "status": "waiting_for_approval",
  "current_step": "send_email",
  "completed_steps": ["research", "draft"]
}
```

Do not rebuild the entire state from historical events on every resume.

---

# Phase 5 — Checkpointing

Checkpoint after meaningful execution boundaries:

```text
LLM decision
tool completion
sandbox operation
human approval
major state transition
```

Example:

```json
{
  "state_version": 4,
  "current_step": "research",
  "pending_action": "search_web",
  "completed_steps": ["discover"],
  "artifacts": ["competitors.csv"]
}
```

The checkpoint is the resume boundary.

---

# Phase 6 — Keep checkpoints small

Avoid:

```text
checkpoint = entire conversation
             + entire filesystem
             + all tool output
```

Prefer:

```text
Checkpoint
├── execution state
├── references
├── current context window
└── artifact references
```

Store large objects separately:

```text
large prompts/results -> object storage
files                  -> workspace/object storage
full history           -> event store
trace                  -> trace backend
```

Recent LangChain runtime work identifies full-snapshot checkpoint growth as an O(N²) problem and uses delta-backed checkpoints with periodic full snapshots. [2]

---

# Phase 7 — Context management

Never send the entire lifetime session to every LLM call.

Use:

```text
Full event history
        |
        v
State builder
        |
        +-- current objective
        +-- completed work
        +-- pending work
        +-- relevant memory
        +-- relevant files
        |
        v
Bounded context
        |
        v
LLM
```

Keep complete history durable while keeping active context bounded.

Temporal's AI reference architecture recommends trimming active message context while preserving workflow history, and recommends `continue_as_new` for very long sessions. [3]

---

# Phase 8 — Resume

API:

```http
POST /sessions/{session_id}/messages
```

Server:

```text
1. Authenticate
2. Load session
3. Validate state
4. Create/resume run
5. Load checkpoint
6. Load relevant context
7. Schedule worker
8. Stream events
```

Worker:

```text
load checkpoint
      |
restore state
      |
restore workspace
      |
continue agent loop
```

The worker does not need to be the process that originally created the session.

---

# Phase 9 — Stateless workers

Bad:

```text
worker memory = session state
```

Good:

```text
worker
   |
   +-- load state
   +-- execute
   +-- checkpoint
   +-- emit events
   +-- exit
```

Workers should be disposable. This enables horizontal scaling, rolling deployments and worker replacement.

---

# Phase 10 — Queue

```text
Session API
    |
    v
PostgreSQL
    |
    v
Queue
    |
    v
Agent Workers
```

Queue message:

```json
{
  "session_id": "s123",
  "run_id": "r456",
  "checkpoint_id": "cp12",
  "attempt": 2
}
```

Do not put the entire conversation into the queue.

---

# Phase 11 — Leases and heartbeats

Use:

```text
worker_id
lease_until
heartbeat_at
```

Execution:

```text
QUEUED
   |
   v
CLAIMED
   |
   v
RUNNING
```

If:

```text
now > lease_until
```

the run becomes recoverable.

Heartbeat every N seconds.

---

# Phase 12 — Sandbox lifecycle

Recommended:

```text
Session
 |
 +-- durable workspace
 |
 +-- ephemeral sandbox
```

Example:

```text
Session S1
 |
Workspace W1
 |
 +-- Run R1 -> Sandbox SB1
 |
 +-- Run R2 -> Sandbox SB2
```

Workspace survives; sandbox can be recreated.

OpenAI's current Sandbox Agents architecture explicitly models persistent workspace separately from sandbox execution. [4]

---

# Phase 13 — Workspace recovery

When creating a new sandbox:

```text
create sandbox
     |
restore/mount workspace
     |
restore environment configuration
     |
restore agent state
     |
resume run
```

Keep a workspace abstraction:

```python
class WorkspaceStore:
    async def read(...)
    async def write(...)
    async def snapshot(...)
    async def restore(...)
```

This allows local object storage to later become GCS/S3/R2.

---

# Phase 14 — Human approval

Agent:

```text
research
  |
draft
  |
approval required
```

Persist:

```text
run.status = WAITING_FOR_APPROVAL
checkpoint = cp42
```

Worker exits.

Later:

```http
POST /runs/{run_id}/resume
```

Create/schedule a new worker.

```text
checkpoint cp42
      |
      v
agent resumes
```

OpenAI's current Agents SDK provides serializable `RunState` specifically as a durable pause/resume boundary. [5][6]

---

# Phase 15 — External waits

Never keep a worker alive while waiting for:

```text
human
webhook
payment
email
scheduled time
external job
```

Instead:

```text
Agent
 |
checkpoint
 |
WAITING
 |
worker exits
 |
external event
 |
resume
```

Temporal is designed around exactly this durable pause/resume model. [7]

---

# Phase 16 — Execution backend abstraction

Define:

```python
class ExecutionBackend:
    async def start(...)
    async def resume(...)
    async def pause(...)
    async def cancel(...)
    async def signal(...)
```

Implement initially:

```text
PostgresQueueExecutionBackend
```

Later:

```text
TemporalExecutionBackend
```

This avoids coupling your agent core to Temporal.

---

# Phase 17 — When to introduce Temporal

Start with:

```text
PostgreSQL
+
queue
+
checkpoints
+
leases
```

Consider Temporal/Dapr/Restate/DBOS when you require:

```text
hours/days/weeks
human approval
timers
complex retries
scheduled workflows
many dependent activities
subagents
strong recovery guarantees
```

OpenAI currently documents Temporal, Dapr, Restate and DBOS integrations for durable long-running agent execution. [8]

---

# Phase 18 — Session as a long-lived workflow

A useful model:

```text
Workflow ID = session_id
```

User sends:

```text
message 1
```

The session workflow handles it.

Later:

```text
message 2
```

The same durable session handles it.

Temporal's AI reference architecture demonstrates a long-lived workflow keyed by a per-session ID, with user turns delivered into that workflow. [3]

Do not let workflow history grow without bound.

---

# Phase 19 — Double texting

Example:

```text
User: Research X
       |
       v
Agent running...

User: Actually research Y
```

Initial policy:

```text
one active run per session
+
queue subsequent messages
```

Later support:

```text
interrupt
merge
cancel-and-replace
concurrent runs
```

Production agent runtimes treat concurrency/double-texting as a runtime capability. [9]

---

# Phase 20 — Session locking

Maintain:

```text
session_id
active_run_id
lock_version
```

Before starting a run:

```text
if active_run_id != null:
    queue message
else:
    create run
```

Use optimistic concurrency/version checks so two API requests cannot accidentally start two runs.

---

# Phase 21 — Agent version pinning

Suppose:

```text
Day 1
Agent v12
Run started
```

Then:

```text
Day 3
Agent v13 deployed
```

Recommended:

```text
running/paused run -> v12
new run             -> v13
```

Store:

```text
agent_version
prompt_version
tool_schema_version
runtime_version
sandbox_image_digest
```

OpenAI's HITL guidance recommends storing an agent-definition/SDK version marker alongside serialized pending state for long-lived approvals. [6]

---

# Phase 22 — Idempotent tools

Long-running agents make duplicate execution dangerous.

Use:

```text
idempotency_key = run_id + step_id
```

Tool execution record:

```text
tool_execution_id
idempotency_key
status
result_ref
```

Before execution:

```text
if key already completed:
    return stored result
```

This is essential for side effects such as:

```text
send_email
create_ticket
charge_card
deploy
delete
```

---

# Phase 23 — Tool result persistence

Do not rely on worker memory.

```text
tool call
   |
   v
tool execution record
   |
   +-- status
   +-- result_ref
   +-- error
   +-- duration
```

Large results:

```text
result_ref -> object storage
```

Small results:

```text
PostgreSQL / workflow state
```

---

# Phase 24 — Session memory vs run state

Keep these separate:

```text
Run state
    = what is happening now

Session memory
    = useful context in this session

Long-term memory
    = knowledge retained across sessions

Event history
    = what happened

Trace
    = observability/debugging

Workspace
    = files/artifacts
```

Do not put everything into one JSON blob.

---

# Phase 25 — Streaming and reconnect

Use SSE/WebSocket:

```text
POST message
      |
      v
run created
      |
      v
event stream
```

Events:

```text
run.started
tool.started
tool.completed
artifact.created
assistant.delta
run.completed
```

Client tracks:

```text
last_seen_sequence
```

On reconnect:

```http
GET /sessions/{id}/events?after=42
```

Never make the live stream the only copy of events.

---

# Phase 26 — Session API

Recommended:

```http
POST   /sessions
GET    /sessions/{id}

POST   /sessions/{id}/messages

GET    /sessions/{id}/events
GET    /sessions/{id}/runs

POST   /runs/{id}/pause
POST   /runs/{id}/resume
POST   /runs/{id}/cancel

GET    /runs/{id}
GET    /runs/{id}/state

GET    /sessions/{id}/workspace
```

---

# Phase 27 — Multi-tenancy and quotas

Every durable object should carry:

```text
tenant_id
project_id
user_id
```

Apply authorization before reading:

```text
session
run
event
checkpoint
workspace
artifact
trace
```

Quotas:

```text
max sessions/user
max concurrent runs/user
max runtime/run
max tokens/run
max workspace size
max artifact count
max sandbox CPU
max sandbox memory
```

---

# Phase 28 — Scheduler

Eventually introduce:

```text
Session Scheduler
```

Responsibilities:

```text
queued runs
stale runs
scheduled runs
retryable runs
waiting runs
quota enforcement
```

Architecture:

```text
Scheduler
   |
   +-- queued
   +-- stale
   +-- retry
   +-- scheduled
          |
          v
        Queue
```

---

# Phase 29 — Scheduling future work

Persist:

```text
scheduled_run
run_at
```

Do not keep a worker alive overnight.

Scheduler wakes the run later.

Durable workflow systems such as Temporal provide timers and scheduling primitives for this use case. [7]

---

# Phase 30 — Long-history compaction

Very long sessions can create huge execution histories.

Use:

```text
history
  |
  v
periodic snapshot
  |
  v
new active generation
```

Keep:

```text
session_id = stable
generation = incremented
```

If using Temporal, use `continue_as_new` for long-running histories. If not, implement equivalent checkpoint + archive + compaction behavior. [3]

---

# Phase 31 — Delta checkpoints

For long sessions:

```text
Step 1 -> full snapshot
Step 2 -> delta
Step 3 -> delta
...
Step K -> full snapshot
```

Benefits:

```text
lower storage
faster writes
bounded resume work
```

This is motivated by LangChain's recent delta-backed checkpoint architecture. [2]

---

# Phase 32 — Deployment safety

Never assume:

```text
worker lifetime >= session lifetime
```

Deployment:

```text
Old worker
    |
    +-- checkpoint
    +-- finish safe step
    +-- exit

New worker
    |
    +-- consume queued work
    +-- resume checkpoint
```

This makes rolling deployments safe.

---

# Phase 33 — Local development

Docker Compose:

```text
api
postgres
redis
worker
sandbox
seaweedfs
otel-collector
clickhouse
```

Mandatory reliability test:

```text
start session
run agent
kill worker
restart worker
resume session
```

Expected:

```text
crash != session loss
```

---

# Phase 34 — GCP production

Initial deployment:

```text
Cloud Run / GKE
      |
      +-- API
      +-- workers
      +-- scheduler

Cloud SQL
      |
      +-- sessions
      +-- runs
      +-- checkpoints
      +-- events

GCS
      |
      +-- workspace payloads
      +-- artifacts
      +-- large tool results
```

Sandbox workers should be isolated from the API/control plane.

---

# Phase 35 — Failure scenarios

Test:

### Worker crash

```text
run
 ↓
checkpoint
 ↓
worker dies
 ↓
resume
```

### API crash

```text
user message
 ↓
DB commit
 ↓
API dies
 ↓
scheduler sees queued run
 ↓
worker executes
```

### Sandbox crash

```text
sandbox dies
 ↓
workspace survives
 ↓
new sandbox
 ↓
resume
```

### Human waits three days

```text
checkpoint
 ↓
no worker
 ↓
approval
 ↓
new worker
 ↓
resume
```

### Deployment

```text
old worker -> checkpoint -> terminate
new worker -> resume
```

---

# Phase 36 — Production invariants

Guarantee:

```text
1. A session is never tied to one worker.
2. A workspace is never tied to one sandbox.
3. A run can resume from a durable checkpoint.
4. Duplicate delivery cannot duplicate idempotent tool actions.
5. User messages are durable before execution begins.
6. Trace loss cannot corrupt session state.
7. Agent version is pinned for a running workflow.
8. Large payloads are referenced rather than embedded everywhere.
9. Active context is bounded even when history is huge.
10. Long waits consume no worker resources.
```

---

# Phase 37 — Recommended stack

For your initial platform:

```text
FastAPI
PostgreSQL
Redis / task queue
Docker sandbox
SeaweedFS locally
GCS in production
OpenTelemetry
ClickHouse
SSE/WebSocket
```

Later:

```text
Temporal
Kubernetes
distributed sandbox scheduler
advanced memory
```

---

# Phase 38 — Final architecture

```text
                             USER
                              |
                              v
                       +--------------+
                       | Session API  |
                       +------+-------+
                              |
                +-------------+-------------+
                |             |             |
                v             v             v
           PostgreSQL     Event Store    Workspace
                |             |             |
                |             |             v
                |             |        Object Storage
                |             |
                +-------------+
                       |
                       v
                Durable Executor
                       |
                       v
                    Queue
                       |
              +--------+--------+
              |                 |
              v                 v
        Agent Worker       Scheduler
              |
       +------+------+
       |             |
       v             v
    Sandbox        Tools
       |
       v
   Workspace
       |
       v
   Artifacts

              ALL EXECUTION
                    |
                    v
             OpenTelemetry
                    |
                    v
              Trace Backend
```

---

# Phase 39 — Implementation order

```text
1. Agent / Environment / Session / Run models
2. Session API
3. PostgreSQL state
4. Event log
5. Run checkpoints
6. Stateless workers
7. Queue
8. Worker leases + heartbeats
9. Workspace abstraction
10. Sandbox recreation
11. Resume after crash
12. Human pause/resume
13. Streaming + reconnect
14. Agent version pinning
15. Idempotent tools
16. Scheduler
17. Context compaction
18. Delta checkpoints
19. Observability integration
20. Load/failure testing
21. GCP deployment
22. Temporal integration when needed
```

---

# Phase 40 — What NOT to build

Do not build:

```text
one permanent Python process per session
```

Do not store:

```text
entire session state only in RAM
```

Do not make:

```text
sandbox = session
```

Do not make:

```text
trace database = execution database
```

Do not keep:

```text
worker alive during human/external waits
```

Do not send:

```text
entire lifetime conversation to every LLM call
```

Do not let:

```text
agent version float while a run is paused
```

Do not make:

```text
tool side effects non-idempotent
```

---

# Phase 41 — Final design principle

```text
                DURABLE SESSION
                      |
             +--------+--------+
             |                 |
             v                 v
        Durable State      Workspace
             |
             v
       Durable Run
             |
             v
      Replaceable Worker
             |
             v
      Replaceable Sandbox
```

A process is temporary.

A sandbox is temporary.

A worker is temporary.

**The session, state, history, workspace and artifacts are durable.**

That is what lets the same session survive worker crashes, container crashes, sandbox crashes, deployments, autoscaling, machine replacement, human waiting, network failures and days/weeks of execution.

---

# References

[1] LangChain — The Runtime Behind Production Deep Agents, April 2026  
https://www.langchain.com/blog/runtime-behind-production-deep-agents

[2] LangChain — Delta Channels: Evolving Our Runtime for Long-Running Agents, May 2026  
https://www.langchain.com/blog/delta-channels-evolving-agent-runtime

[3] Temporal — AI Agent Reference Architecture  
https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture

[4] OpenAI Agents SDK — Sandbox Agents  
https://openai.github.io/openai-agents-python/sandbox_agents/

[5] OpenAI Agents SDK — RunState  
https://openai.github.io/openai-agents-python/ref/run_state/

[6] OpenAI Agents SDK — Human-in-the-loop  
https://openai.github.io/openai-agents-python/human_in_the_loop/

[7] Temporal — Durable Execution  
https://temporal.io/

[8] OpenAI Agents SDK — Running Agents / Durable Execution Integrations  
https://openai.github.io/openai-agents-python/running_agents/

[9] LangSmith Deployment — Production Agent Runtime  
https://www.langchain.com/langsmith/deployment

---

# Recommended evolution

Start with:

```text
PostgreSQL
+
Queue
+
Stateless Agent Workers
+
Explicit Checkpoints
+
Workspace abstraction
+
Replaceable Sandbox
+
OpenTelemetry
```

Then:

```text
simple workloads
       |
       v
Postgres + Queue
       |
       v
complex long-running workloads
       |
       v
Temporal
```

This gives you a simple local development model without locking the platform into a process-per-session architecture.
