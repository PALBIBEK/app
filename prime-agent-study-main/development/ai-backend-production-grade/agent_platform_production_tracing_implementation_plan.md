# Production-Grade Agent Tracing & Observability — Phase-by-Phase Plan

> **Shipping owner (2026-08-30):** [`D13.md`](./D13.md) — current status, acceptance, and MVP order.  
> This file remains the detailed 35-phase design reference. Prefer D13 for “what’s done / what’s missing.”

## Goal

Build a production-grade tracing system for an agent platform that captures agent runs, LLM calls, tool calls, retrieval, planning, sandbox execution, errors, approvals, guardrails, artifacts, evaluations, tokens and cost.

Core principle:

```text
Your Agent Trace Contract
        +
OpenTelemetry
        +
OTLP
        +
Queryable trace backend
```

Do not invent a proprietary wire protocol. Future AGI's traceAI is a strong reference because it adds AI-specific semantic conventions and instrumentation on top of OpenTelemetry, while remaining compatible with OTel backends. [1][2]

---

# 1. Target Architecture

## Development

```text
Agent Runtime
     |
Trace SDK / Contract
     |
OpenTelemetry SDK
     |
    OTLP
     |
OTel Collector
   /       \
  v         v
Jaeger   ClickHouse
```

## Production

```text
Agent Runtime
     |
Trace SDK / Contract
     |
OpenTelemetry SDK
     |
    OTLP
     |
OTel Collector / Gateway
     |
Queue / Buffer
     |
Trace Workers
     |
ClickHouse
     |
Trace API
     |
Frontend
```

Future AGI's documented architecture similarly separates traceAI instrumentation, OTLP trace ingestion, ClickHouse for spans/time-series, PostgreSQL for metadata, and Redis/RabbitMQ/Temporal for other platform state/jobs. [3]

---

# 2. Core Decision: OTel Underneath, Your Contract Above It

Do not build:

```text
Agent -> Custom JSON protocol -> Custom trace DB
```

Build:

```text
Agent
  |
Your semantic trace contract
  |
OpenTelemetry spans
  |
OTLP
  |
Collector
  |
Backend
```

OpenTelemetry provides trace IDs, span IDs, parent/child relationships, propagation, batching/export and OTLP.

Your contract defines what your platform means by:

- agent.run
- agent.step
- llm.call
- tool.call
- retrieval
- sandbox.exec
- artifact.created
- human.approval
- guardrail.check
- evaluation

Future AGI's traceAI follows this general architecture: AI-specific conventions on top of OTel rather than a proprietary transport. [1][2]

---

# 3. Phase 1 — Define the Trace Contract

Recommended span/event types:

```text
agent.run
agent.step

llm.call
llm.stream

tool.call
tool.result

retrieval.query
retrieval.result
reranker.call

sandbox.exec
sandbox.file_change

artifact.created
artifact.modified
artifact.deleted

human.approval
guardrail.check

http.request
db.query

evaluation.run
evaluation.score
```

Use spans for operations with duration/causal context. Use span events or linked records for instantaneous facts.

Every custom event should contain:

```text
schema_version
trace_id
span_id
run_id
scope_id
timestamp
type
status
```

---

# 4. Phase 2 — Agent Run

Top-level execution:

```json
{
  "schema_version": "agent.trace.v1",
  "type": "agent.run",
  "run_id": "run_123",
  "agent_id": "research-agent",
  "agent_version": "2026.08.30",
  "scope_id": "scope_456",
  "harness": "codex",
  "status": "success"
}
```

Use the OTel trace ID as the canonical execution correlation ID. Keep your domain `run_id` separately.

---

# 5. Phase 3 — LLM and Tool Spans

LLM:

```text
gen_ai.system
gen_ai.request.model
gen_ai.response.model
gen_ai.operation.name
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

Future AGI documents these kinds of standardized AI attributes. [2][4]

Tool:

```json
{
  "type": "tool.call",
  "tool_name": "search",
  "tool_version": "v2",
  "status": "success",
  "duration_ms": 350
}
```

Capture arguments/results carefully because they may contain secrets or large payloads.

---

# 6. Phase 4 — Sandbox and Artifact Tracing

For your platform this is especially important.

```text
Agent Run
   |
Tool Call
   |
Sandbox Execution
   |
/workspace/report.pdf
   |
artifact.created
   |
artifact_id
   |
Workspace File
   |
Object Storage
```

Sandbox span:

```json
{
  "type": "sandbox.exec",
  "sandbox_id": "sb_123",
  "command_hash": "...",
  "exit_code": 0,
  "duration_ms": 820
}
```

Do not put huge command output into every span. Store large payloads in object storage and reference them:

```text
span
 |
 +-- output_ref
 +-- payload_hash
 +-- exit_code
 +-- duration
```

This connects your tracing system directly to the workspace system.

---

# 7. Phase 5 — Build an Internal Trace SDK

Suggested package:

```text
packages/tracing/
├── context.py
├── tracer.py
├── spans.py
├── attributes.py
├── payloads.py
├── exporters.py
└── schema.py
```

Expose a small API:

```python
with tracer.agent_run(...) as run:
    with run.llm_call(...) as span:
        ...

    with run.tool_call(...) as span:
        ...

    with run.sandbox_exec(...) as span:
        ...
```

Agent code should depend on the Trace SDK, not ClickHouse.

---

# 8. Phase 6 — Automatic + Manual Instrumentation

Use both.

Automatic:

```text
OpenAI
Anthropic
HTTP
Postgres
Redis
agent frameworks
```

Manual:

```text
agent.step
tool.call
sandbox.exec
artifact.created
human.approval
```

Future AGI explicitly documents automatic framework instrumentors plus manual instrumentation for custom application logic, with both feeding the same OTel provider and trace tree. [2]

---

# 9. Phase 7 — Context Propagation

One request may become:

```text
API
 |
Agent worker
 |
 +-- LLM
 +-- Tool service
 +-- Sandbox
 |     +-- subprocess
 +-- Retrieval
```

All must preserve:

```text
trace_id
parent_span_id
```

Use standard OTel/W3C propagation rather than custom headers.

Result:

```text
Agent Run
├── LLM
├── Tool
│   └── HTTP
├── Retrieval
└── Sandbox
    └── Process
```

Future AGI highlights this same benefit: AI spans can join normal HTTP/database/queue spans under the same trace ID. [5]

---

# 10. Phase 8 — Local OTel Collector

Local Docker Compose:

```text
api
worker
postgres
seaweedfs
clickhouse
otel-collector
jaeger
```

Flow:

```text
Agent
 -> OTel SDK
 -> OTLP
 -> Collector
 -> Jaeger
```

Also send to ClickHouse so the production query model can be tested locally.

---

# 11. Phase 9 — Production Trace Gateway

Use a dedicated ingestion boundary:

```text
Agents
  |
OTLP
  |
OTel Collector / Gateway
  |
 +-- authentication
 +-- validation
 +-- PII filtering
 +-- enrichment
 +-- sampling
 +-- routing
  |
Queue / Buffer
```

Never make the agent synchronously wait for ClickHouse.

---

# 12. Phase 10 — Asynchronous Persistence

Preferred:

```text
Agent
 |
Collector
 |
Queue
 |
Trace Worker
 |
ClickHouse
```

If ClickHouse is temporarily unavailable, the queue absorbs the backlog instead of breaking agent execution.

Future AGI's documented platform uses separate job/state infrastructure alongside ClickHouse, illustrating this separation. [3]

---

# 13. Phase 11 — ClickHouse Data Model

Conceptual table:

```text
spans
-----
trace_id
span_id
parent_span_id

timestamp_start
timestamp_end
duration_ms

tenant_id
project_id
scope_id
user_id

agent_id
agent_version
run_id
session_id

span_type
span_name
status

model
provider
tool_name

input_tokens
output_tokens
cache_tokens

error_type
error_message

attributes_json
payload_ref
```

Use stable, frequently queried fields as columns. Put rare/custom attributes in JSON/Map. Put large payloads in object storage.

Future AGI documents ClickHouse as the storage layer for spans/time-series and PostgreSQL for metadata. [3]

---

# 14. Phase 12 — PostgreSQL vs ClickHouse

PostgreSQL:

```text
users
projects
agents
agent_versions
runs
permissions
trace_index
evaluation_definitions
```

ClickHouse:

```text
spans
trace events
LLM calls
tool calls
retrieval
latency
tokens
cost
```

Do not make PostgreSQL the primary long-term trace analytics store.

---

# 15. Phase 13 — Trace API

Frontend should never query ClickHouse directly.

Build:

```text
GET /traces
GET /traces/{trace_id}
GET /traces/{trace_id}/spans
GET /runs/{run_id}/trace
GET /agents/{agent_id}/traces
GET /scopes/{scope_id}/traces
```

API handles:

```text
authentication
authorization
tenant isolation
pagination
query limits
redaction
```

---

# 16. Phase 14 — Trace UI

Minimum:

```text
Trace list
-----------------------------
Time | Agent | Duration | Status

Trace detail
-----------------------------
Agent Run
├── Agent Step
├── LLM Call
├── Tool Call
│   └── HTTP
├── Retrieval
├── Sandbox Exec
│   └── artifact.created
└── Final LLM Call
```

Span detail:

```text
duration
status
model
tokens
cost
tool
arguments
result
errors
artifact references
```

Payload access must be authorization-controlled.

---

# 17. Phase 15 — Cost Tracking

Calculate cost from LLM spans:

```text
provider
model
input tokens
output tokens
cache tokens
       |
       v
pricing table
       |
       v
estimated cost
```

Store:

```text
input_cost
output_cost
total_cost
currency
pricing_version
```

Do not hard-code pricing in the trace schema.

---

# 18. Phase 16 — Evaluation Integration

Trace becomes evaluation input:

```text
Agent Run
   |
Trace
   |
Evaluation
   |
Score
```

Reference the trace/span rather than copying the whole trace:

```json
{
  "evaluation_id": "eval_123",
  "trace_id": "...",
  "span_id": "...",
  "metric": "tool_selection",
  "score": 0.92
}
```

Future AGI's ecosystem connects tracing to evaluations and optimization. [6]

---

# 19. Phase 17 — Failure Analysis

Create a failure taxonomy:

```text
agent.failure
├── llm_failure
├── tool_failure
├── retrieval_failure
├── sandbox_failure
├── timeout
├── authorization_failure
├── guardrail_failure
└── application_failure
```

Then answer:

```text
Which agents fail most?
Which tools fail most?
Which model produces most retries?
Which sandbox image fails most?
Which agent version regressed?
```

---

# 20. Phase 18 — Sampling

For your initial ~50 daily users:

**Keep 100% of traces.**

Later:

```text
successful normal traces -> sample
slow traces              -> retain
failed traces            -> retain
security traces          -> retain
evaluation traces        -> retain
```

Prefer tail-based sampling when introducing sampling because the collector can see the complete trace before deciding retention. Future AGI's current observability guidance discusses collector-side sampling and tiered retention. [7]

---

# 21. Phase 19 — Payload Storage

Do not keep huge prompts/responses directly in ClickHouse forever.

Use:

```text
Span
 |
 +-- metadata
 +-- token counts
 +-- hash
 +-- payload_ref
              |
              v
        Object Storage
```

Example:

```json
{
  "input_tokens": 92000,
  "output_tokens": 400,
  "prompt_sha256": "...",
  "prompt_ref": "traces/2026/08/30/abc/input.json"
}
```

Use your existing abstraction:

```text
ObjectStorage
├── SeaweedFS locally
└── GCS in production
```

---

# 22. Phase 20 — PII and Secret Filtering

Tracing can become the most sensitive database in the platform.

Collector pipeline:

```text
OTLP
 |
PII/secret filtering
 |
Storage
```

Never blindly capture:

```text
Authorization headers
API keys
cloud credentials
session cookies
database passwords
```

Use allowlists where possible.

Future AGI's security policy explicitly identifies credential leakage in trace spans, data exfiltration through instrumentors and OTLP injection as security concerns. [8]

---

# 23. Phase 21 — Multi-Tenancy

Every span should be associated with:

```text
tenant_id
project_id
scope_id
user_id
agent_id
run_id
```

Authorization:

```text
User
 -> Tenant
 -> Project
 -> Scope
 -> Trace
```

Never trust tenant/scope identifiers supplied by an agent.

Inject them from authenticated control-plane context.

---

# 24. Phase 22 — Duplicate/Retry Handling

Design for duplicate delivery.

Logical identity:

```text
trace_id + span_id
```

Custom events:

```text
event_id
```

Persistence should tolerate exporter retries.

---

# 25. Phase 23 — Trace Schema Versioning

Use:

```text
agent.trace.v1
agent.trace.v2
```

Every custom event carries:

```text
schema_version
```

Rules:

```text
additive change -> compatible
breaking change -> new version
```

Never silently change field meaning.

---

# 26. Phase 24 — Replay/Reproducibility

A replayable run should reference:

```text
agent version
model
tool versions
sandbox image digest
workspace snapshot/version
configuration
trace
```

Conceptually:

```text
run
├── agent_version
├── sandbox_image
├── workspace_version
├── model
├── tool_versions
└── trace
```

Exact LLM determinism is not guaranteed. Aim first for reproducible context reconstruction.

---

# 27. Phase 25 — Artifact Lineage

Represent:

```text
span
 |
 +-- produced artifact
 +-- consumed artifact
 +-- modified artifact
```

Example:

```text
retrieval
   |
LLM
   |
sandbox.exec
   |
artifact.created(report.pdf)
```

This turns tracing into execution + provenance.

Recent research also argues for structured state deltas and artifact/claim lineage as complements to conventional telemetry. [12][13]

---

# 28. Phase 26 — GCP Production

Logical deployment:

```text
GCP
├── API
├── Agent workers
├── Sandbox workers
├── Cloud SQL PostgreSQL
├── GCS
├── OTel Collector
├── ClickHouse
├── Queue
├── Trace workers
└── Trace API
```

Separate scaling dimensions:

```text
API load
   !=
trace ingestion load
   !=
sandbox load
```

---

# 29. Phase 27 — Trace Ingestion Scaling

Initial deployment:

```text
1–2 collectors
1 trace worker
1 ClickHouse instance
```

Later:

```text
Load Balancer
      |
+-----+-----+
|           |
Collector A Collector B
|           |
+-----+-----+
      |
    Queue
      |
+-----+-----+
|           |
Worker A  Worker B
      |
 ClickHouse
```

---

# 30. Phase 28 — Reliability

Initial SLO targets:

```text
Trace ingestion availability > 99.9%
Trace loss                  < 0.01%
Trace ingestion p95         < 1 sec
Trace query p95             < 2 sec
```

Tracing must never become a hard dependency for agent execution.

Use:

```text
batching
retry
bounded queue
backpressure
non-blocking export
```

---

# 31. Phase 29 — Backpressure

When storage is unavailable:

```text
Collector
 |
Queue fills
 |
 +-- retry
 +-- bounded backpressure
 +-- temporary sampling
 +-- spill/buffer
```

Set limits:

```text
max queue size
max payload size
max retry duration
max batch size
```

Never allow tracing to consume unbounded agent memory.

---

# 32. Phase 30 — Retention

Example starting policy:

```text
Raw traces       30 days
Failure traces   90 days
Eval traces      90 days
Aggregates       1 year
```

Tune by cost/compliance.

Large payloads can have shorter retention than metadata.

---

# 33. Phase 31 — Observability of the Observability System

Monitor:

```text
collector_received_spans
collector_dropped_spans
collector_queue_size
collector_export_errors

clickhouse_insert_latency
clickhouse_insert_errors

trace_query_latency
trace_query_errors

trace_payload_bytes
```

Otherwise tracing can silently fail while the agent appears healthy.

---

# 34. Phase 32 — Testing

## Unit

```text
span creation
context propagation
schema validation
redaction
serialization
cost calculation
```

## Integration

```text
Agent
 -> OTel
 -> Collector
 -> ClickHouse
 -> Trace API
```

## Failure

Test:

```text
ClickHouse unavailable
Collector unavailable
Queue full
network timeout
duplicate span
malformed span
huge payload
secret in prompt
agent crash
sandbox crash
```

## Load

Start:

```text
10 concurrent runs
25 concurrent runs
50 concurrent runs
```

Measure:

```text
spans/sec
bytes/sec
collector CPU
ClickHouse CPU
insert latency
query latency
memory
dropped spans
```

---

# 35. Phase 33 — Security Threat Model

Assume generated agent content is untrusted.

Protect against:

```text
OTLP injection
attribute explosion
cardinality explosion
secret leakage
oversized payloads
malicious queries
cross-tenant reads
```

Never put arbitrary user-generated values into metric labels.

---

# 36. Phase 34 — Evaluation + Optimization Loop

Long-term:

```text
Production Agent
      |
    Trace
      |
 Evaluation
      |
   Score
      |
Failure dataset
      |
Agent/prompt optimization
```

Future AGI explicitly connects trace data to evaluations and optimization, making this a useful long-term architecture reference. [6]

---

# 37. Phase 35 — Future Features

After the base system is stable:

- trace replay
- agent version comparison
- regression detection
- automatic failure clustering
- agent debugging
- causal artifact lineage
- trace-to-dataset export
- agent-native telemetry
- claim/evidence provenance

Do not build these before the basic OTel pipeline works.

---

# 38. Development vs Production

| Component | Development | Production |
|---|---|---|
| Trace SDK | local package | versioned package |
| Transport | OTLP | OTLP |
| Collector | Docker | HA collectors |
| Backend | Jaeger + ClickHouse | ClickHouse |
| Metadata | PostgreSQL | Cloud SQL |
| Payloads | SeaweedFS | GCS |
| Queue | optional | managed/HA |
| Sampling | 100% | tail-based later |
| Retention | short | policy-based |
| PII filtering | basic | mandatory |
| Auth | local | tenant-aware |
| Trace API | FastAPI | autoscaled |
| Monitoring | Docker | Cloud Monitoring |
| Secrets | .env | Secret Manager |

---

# 39. Recommended Repository Structure

```text
agent-platform/
├── apps/
│   ├── api/
│   ├── worker/
│   ├── trace-api/
│   └── web/
├── packages/
│   ├── agent-core/
│   ├── tracing/
│   │   ├── schema/
│   │   ├── instrumentation/
│   │   ├── context/
│   │   └── exporters/
│   ├── workspace/
│   └── sandbox/
├── infra/
│   ├── docker/
│   ├── otel/
│   ├── clickhouse/
│   └── gcp/
└── docs/
    └── tracing.md
```

---

# 40. Implementation Order

```text
1. Define trace contract
       ↓
2. Create Trace SDK
       ↓
3. Add OTel provider
       ↓
4. Add agent.run
       ↓
5. Add LLM spans
       ↓
6. Add tool spans
       ↓
7. Add sandbox spans
       ↓
8. Add artifact lineage
       ↓
9. Add context propagation
       ↓
10. Local OTel Collector
       ↓
11. Jaeger
       ↓
12. ClickHouse
       ↓
13. Trace API
       ↓
14. Trace UI
       ↓
15. Payload object storage
       ↓
16. Redaction/security
       ↓
17. Async queue
       ↓
18. Load testing
       ↓
19. GCP deployment
       ↓
20. Evaluations
       ↓
21. Sampling/retention
       ↓
22. Replay/regression tooling
```

---

# 41. What NOT to Build Initially

Avoid:

- proprietary trace transport
- custom distributed tracing protocol
- custom collector
- custom trace database
- multi-region ClickHouse
- aggressive sampling
- LLM-powered trace summarization
- automatic failure clustering
- full replay engine
- custom stream-processing framework

Start with:

```text
OpenTelemetry
+
OTLP
+
Collector
+
ClickHouse
+
PostgreSQL
+
Object Storage
```

---

# 42. Final Target Architecture

```text
                         USER
                           |
                           v
                       Agent API
                           |
                           v
                       Agent Core
                       /        \
                      /          \
                     v            v
                 Agent Run    Workspace
                     |             |
                     |             v
                     |        Object Storage
                     |
                     v
                  Trace SDK
                     |
                     v
               OpenTelemetry
                     |
                    OTLP
                     |
                     v
              OTel Collector
                     |
                +----+----+
                |         |
                v         v
              Queue     Metrics
                |
                v
          Trace Workers
                |
                v
            ClickHouse
                |
                v
             Trace API
                |
                v
             Trace UI
                |
                v
           Evaluation
                |
                v
        Agent Improvement
```

---

# 43. Recommended Stack for Your Platform

## Local

```text
FastAPI
PostgreSQL
SeaweedFS
Docker Sandbox
OpenTelemetry SDK
OpenTelemetry Collector
Jaeger
ClickHouse
```

## Production

```text
GCP API
Cloud SQL
GCS
Sandbox workers
OpenTelemetry Collector
ClickHouse
Queue
Trace workers
Trace API
```

Keep these abstractions:

```text
ObjectStorage
  ├── SeaweedFSStorage
  └── GCSStorage

Sandbox
  ├── DockerSandbox
  └── GcpSandbox

TraceExporter
  └── OTLP

TraceBackend
  └── ClickHouse
```

The Agent Core should not know which storage, sandbox or tracing backend is being used.

---

# References

[1] Future AGI — traceAI GitHub  
https://github.com/future-agi/traceAI

[2] Future AGI — traceAI concepts  
https://docs.futureagi.com/docs/tracing/concepts/traceai/

[3] Future AGI — main platform repository  
https://github.com/future-agi/future-agi

[4] Future AGI — traceAI / OpenTelemetry tracing  
https://futureagi.com/blog/traceai-opentelemetry-tracing-2026/

[5] Future AGI — LLM observability with OpenTelemetry  
https://futureagi.com/blog/llm-app-observability-otel-2026/

[6] Future AGI — observability and evaluation concepts  
https://docs.futureagi.com/docs/tracing/concepts/

[7] Future AGI — LLM observability guide  
https://futureagi.com/blog/what-is-llm-observability-ultimate-guide-2026/

[8] Future AGI — traceAI security  
https://github.com/future-agi/traceAI/security

[9] OpenTelemetry  
https://opentelemetry.io/

[10] OpenTelemetry OTLP  
https://opentelemetry.io/docs/specs/otlp/

[11] OpenTelemetry GenAI semantic conventions  
https://opentelemetry.io/docs/specs/semconv/gen-ai/

[12] Agent-Native Telemetry — 2026 research  
https://arxiv.org/abs/2608.16178

[13] Artifact-centered Claim-aware Observability — 2026 research  
https://arxiv.org/abs/2608.18312
