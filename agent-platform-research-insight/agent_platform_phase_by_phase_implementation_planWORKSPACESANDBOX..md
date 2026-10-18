# Agent Platform: Workspace + Sandbox Implementation Plan

## Goal

Build a multi-user agent platform where every user gets an isolated workspace and sandbox/computer. The agent can create and modify files such as code, Markdown, PDFs, images, CSVs, and other artifacts. The system should work locally during development and later run on GCP for roughly 50 daily users, with the ability to scale independently.

The core architectural principle is:

- **Workspace = durable user data**
- **Sandbox = agent execution environment**
- **PostgreSQL = metadata, identity, scope, sessions, permissions**
- **Object storage = durable file bytes**
- **Sandbox filesystem = the interface the agent naturally uses**
- **Control plane = decides which user/scope can access which sandbox and data**

This follows patterns visible in YC's open-source QM harness, Daytona's persistent sandbox/volume architecture, and filesystem-agent designs from Vercel. QM explicitly gives each person/room scoped files, memory, permissions and a durable sandbox; Daytona separates sandbox compute from persistent S3-backed volumes; Vercel demonstrates giving an agent a sandbox plus bash/filesystem access. [1][2][3]

---

# Phase 0 — Freeze the architecture before coding

## Target logical model

```text
User
 |
 v
Agent API / Core
 |
 +---- Scope / Workspace
 |       |
 |       +---- PostgreSQL metadata
 |       |
 |       +---- Object storage
 |
 +---- Sandbox Manager
          |
          +---- Sandbox for that scope
                    |
                    +---- /workspace
                    +---- tools
                    +---- code
                    +---- packages
                    +---- processes
```

A useful mental model is:

```text
Scope
├── identity
├── permissions
├── workspace
├── memory/session state
└── sandbox
```

This is close to QM's model of personal/shared scopes with scoped files, memory, permissions and a durable sandbox. [1]

## Important design decision

Do **not** make the sandbox filesystem the only source of truth.

Instead:

```text
              Workspace
                 |
        +--------+--------+
        |                 |
   PostgreSQL         Object store
   metadata           actual bytes
        |
        +--------------------+
                             |
                             v
                         Sandbox
                       /workspace
```

The sandbox may contain additional durable computer state, but user-visible artifacts should have a persistence strategy independent of a particular API process.

---

# Phase 1 — Repository and interfaces

Create a monorepo or clearly separated services.

Suggested structure:

```text
agent-platform/
├── apps/
│   ├── api/
│   ├── worker/
│   └── web/
├── packages/
│   ├── workspace/
│   ├── sandbox/
│   ├── storage/
│   ├── auth/
│   └── agent-core/
├── infra/
│   ├── docker/
│   └── gcp/
└── docs/
```

Create interfaces first.

## Object storage interface

```python
class ObjectStorage:
    async def put(self, key, data, content_type=None): ...
    async def get(self, key): ...
    async def delete(self, key): ...
    async def exists(self, key): ...
    async def create_download_url(self, key): ...
    async def create_upload_url(self, key): ...
```

Implement:

```text
SeaweedFS/S3 adapter  -> development
GCS adapter           -> production
```

The application should never depend directly on SeaweedFS or GCS.

## Sandbox interface

```python
class Sandbox:
    async def create(self, scope_id): ...
    async def exec(self, command): ...
    async def read_file(self, path): ...
    async def write_file(self, path, data): ...
    async def delete(self, path): ...
    async def destroy(self): ...
```

Implement:

```text
LocalDockerSandbox -> development
GcpSandbox         -> production
```

The exact production implementation can later be changed without rewriting the agent core.

This follows QM's important pattern: substrates such as harness, session store and sandbox sit behind interfaces and are wired by the deployment. [1][4]

---

# Phase 2 — Workspace data model

Use PostgreSQL for metadata.

Suggested tables:

```text
users
------
id
email
created_at

scopes
------
id
type                  # personal / project / channel
owner_user_id
name
created_at

workspace_files
---------------
id
scope_id
parent_id
name
path
type                  # file / directory
mime_type
size_bytes
object_key
version
created_at
updated_at
deleted_at

agent_runs
----------
id
scope_id
sandbox_id
status
started_at
finished_at

artifacts
---------
id
scope_id
run_id
file_id
artifact_type
metadata_json
created_at
```

Use `scope_id` rather than only `user_id`. This gives you a future path to:

```text
User A personal workspace
Project X shared workspace
Slack channel workspace
```

which is close to QM's personal/shared scope design. [1]

## Object key

Do not use the user's raw path as the security boundary.

Example:

```text
workspaces/{scope_id}/objects/{random_object_id}
```

PostgreSQL maps:

```text
/report.pdf
```

to:

```text
workspaces/scope_123/objects/8c8b...
```

This separates filesystem naming from physical object storage.

---

# Phase 3 — Local development environment

Do NOT use GCP initially.

Run locally:

```text
Docker Compose
├── PostgreSQL
├── SeaweedFS
├── API
├── worker
└── sandbox runtime
```

The storage endpoint should be configured through environment variables.

Example:

```env
STORAGE_BACKEND=s3
STORAGE_ENDPOINT=http://seaweedfs:8333
STORAGE_BUCKET=agent-workspaces
STORAGE_ACCESS_KEY=...
STORAGE_SECRET_KEY=...
```

The application talks through `ObjectStorage`, not through SeaweedFS-specific APIs.

SeaweedFS is useful here because its S3 gateway lets your development environment use an S3-compatible object-storage interface.

## Local success criteria

You should be able to:

1. Create a user.
2. Create a scope.
3. Create a sandbox for the scope.
4. Run the agent.
5. Have the agent execute:
   ```bash
   mkdir -p /workspace/reports
   echo '# Report' > /workspace/reports/report.md
   ```
6. See the file in the web UI.
7. Download the file.
8. Delete/recreate the API process without losing the file.
9. Restart the sandbox and still access the workspace.

---

# Phase 4 — Workspace filesystem behavior

The agent should see a normal filesystem:

```text
/workspace
├── code/
├── data/
├── reports/
└── artifacts/
```

Tell the agent:

```text
/workspace is your persistent user workspace.
Files that should remain available to the user must be placed under /workspace.
Use /tmp for temporary files.
```

This follows the filesystem-agent model: agents are already good at navigating directories and using shell tools, so exposing a filesystem is often more natural than forcing every operation through custom semantic tools. [3][5]

## MVP synchronization model

Initially use:

```text
Persistent object store
        |
        | hydrate
        v
Sandbox /workspace
        |
        | agent works
        v
Sandbox /workspace
        |
        | sync changed files
        v
Persistent object store
```

For small workspaces this is simple and sufficient.

Do NOT build a custom FUSE filesystem in Phase 1.

---

# Phase 5 — Agent run lifecycle

Implement:

```text
POST /scopes/{scope_id}/runs
```

Lifecycle:

```text
1. Authenticate user
2. Resolve scope
3. Authorize access
4. Get/create sandbox
5. Mount or hydrate workspace
6. Start agent harness
7. Agent operates in /workspace
8. Capture artifacts
9. Persist workspace changes
10. Persist run status
11. Return response/artifacts
```

Never let the model itself decide which scope it may access.

The core resolves:

```text
principal -> scope -> grants -> sandbox
```

QM explicitly treats the sandbox and agent software as untrusted for authorization decisions; authorization belongs in the core/control plane. [6]

---

# Phase 6 — Durable sandbox

At this point decide whether your sandbox is:

### Development

Use a Docker container.

```text
scope_123
   |
   v
Docker container
   |
   +-- /workspace
   +-- tools
   +-- packages
```

### Production

Use a sandbox substrate that can provide isolated compute, such as VM/microVM/container workers.

The exact provider is an implementation detail behind `Sandbox`.

## Durable vs ephemeral

For a coding-agent experience, a durable sandbox is valuable:

```text
User A
  |
  v
Sandbox A
  |
  +-- installed packages
  +-- cloned repositories
  +-- caches
  +-- workspace
```

The next run can reuse the same computer.

Daytona follows this model: sandboxes are persistent by default and preserve filesystem, installed packages and repositories; its volumes persist independently of the sandbox. [2]

You can still add:

```text
auto-stop
auto-pause
auto-delete
```

later to control cost.

---

# Phase 7 — Workspace persistence strategy

There are two layers:

## Layer A — Sandbox computer state

Potentially durable:

```text
installed packages
git repositories
caches
tools
```

## Layer B — User workspace

Must be durable independently:

```text
/workspace/report.pdf
/workspace/chart.png
/workspace/code.py
```

For larger scale, use persistent storage mounted into the sandbox.

Daytona uses S3-compatible object storage-backed volumes that can be mounted into multiple sandboxes and persist after sandbox deletion. It recommends shared volumes with per-user/per-workspace subpaths rather than one independent volume for every sandbox. [2][7]

Your long-term model can therefore become:

```text
Object storage
      |
Persistent workspace volume
      |
      +------ Sandbox A
      |
      +------ Sandbox B
```

Do not build this infrastructure yourself initially.

---

# Phase 8 — Artifact system

Make generated outputs first-class.

Example:

```text
Agent Run #42

Created:
✓ report.md
✓ report.pdf
✓ chart.png
✓ analysis.py
```

When the agent creates a file:

```text
/workspace/report.pdf
```

record:

```text
artifact
---------
scope_id
run_id
file_id
artifact_type = document
```

This makes the UI easy to build.

Frontend:

```text
Workspace
├── reports/
│   ├── report.md
│   └── report.pdf
├── images/
│   └── chart.png
└── code/
    └── analysis.py
```

---

# Phase 9 — Upload/download architecture

Do not proxy large file bytes through your API.

### Upload

```text
Browser
   |
   | request upload
   v
API
   |
   | signed upload URL
   v
Browser
   |
   | direct upload
   v
Object storage
```

### Download

```text
Browser
   |
   | request download
   v
API
   |
   | authorization + signed URL
   v
Browser
   |
   v
Object storage
```

Your API controls authorization; object storage handles the bandwidth.

---

# Phase 10 — Local concurrency testing

Before GCP, test locally.

Do not test only one agent.

Test:

```text
10 concurrent runs
25 concurrent runs
50 concurrent runs
```

Measure:

```text
API latency
sandbox creation time
sandbox startup time
workspace hydration time
file upload time
Postgres connections
CPU
RAM
disk
network
agent run duration
failure/retry rate
```

The important distinction is:

```text
50 daily users
```

versus:

```text
50 simultaneous sandboxes
```

You should design for the latter even if your expected workload is only 50 users/day.

---

# Phase 11 — Production containerization

Containerize:

```text
API
worker
sandbox image
frontend
```

Create immutable image versions.

Example:

```text
agent-platform-api:v1
agent-platform-worker:v1
sandbox:v1
```

Avoid mutable production tags as the only deployment reference.

QM's deployment system pins sandbox/container artifacts to immutable image digests, which is a good production practice. [4]

---

# Phase 12 — GCP architecture

For your expected initial scale:

```text
                     Internet
                        |
                        v
                Cloud Load Balancer
                        |
              +---------+---------+
              |                   |
              v                   v
           API #1              API #2
              |                   |
              +---------+---------+
                        |
                  Agent scheduler
                        |
             +----------+----------+
             |          |          |
             v          v          v
          Worker     Worker     Worker
             |          |          |
             v          v          v
          Sandbox    Sandbox    Sandbox
             |          |          |
             +----------+----------+
                        |
                Persistent workspace
                        |
                        v
                 Cloud Storage
```

Use:

```text
Cloud Run       -> stateless API where suitable
Cloud SQL       -> PostgreSQL
Cloud Storage   -> persistent workspace objects
GKE/Compute     -> sandbox workers if your sandbox requires privileged/container/VM control
Cloud Logging   -> logs
Secret Manager  -> credentials
```

Do not assume Cloud Run is the right place for the actual agent sandbox. Sandbox requirements such as privileged execution, long-running processes, custom kernels, or microVM isolation may push that layer toward GKE/Compute Engine or a specialized sandbox provider.

Cloud Run is a good candidate for stateless APIs and scales based on request/compute usage. [8]

---

# Phase 13 — GCP storage adapter

Your local code:

```text
ObjectStorage
   |
   v
SeaweedFS/S3
```

Production:

```text
ObjectStorage
   |
   v
GCS
```

Your workspace service remains:

```python
await storage.put(key, data)
await storage.get(key)
await storage.delete(key)
```

Only dependency wiring changes:

```text
DEV:
ObjectStorage -> SeaweedFSStorage

PROD:
ObjectStorage -> GCSStorage
```

This is why the abstraction must be introduced before you build the workspace service.

---

# Phase 14 — GCP sandbox manager

Build a scheduler:

```text
Agent Run
   |
   v
Sandbox Manager
   |
   +-- find existing sandbox
   |
   +-- start sandbox
   |
   +-- create sandbox
   |
   +-- mount workspace
   |
   +-- execute agent
   |
   +-- stop/retain sandbox
```

Maintain a registry:

```text
sandboxes
---------
id
scope_id
provider
provider_instance_id
status
image_digest
created_at
last_active_at
```

This lets you move from:

```text
1 sandbox per scope
```

to:

```text
sandbox pool
```

later.

---

# Phase 15 — Scaling to 50 users/day

Start with:

```text
2 API instances
1 PostgreSQL
small object storage
small worker pool
dynamic sandbox creation
```

Do not run 50 permanent sandboxes unless usage requires it.

Example:

```text
50 users/day
      |
      v
10 active runs
      |
      v
10 sandboxes
      |
      v
auto-stop when inactive
```

The expensive component is likely sandbox compute, not object storage.

Scale independently:

```text
API capacity      != sandbox capacity
Postgres capacity != sandbox capacity
storage capacity  != sandbox capacity
```

This is the same compute-plane/control-plane separation used by Daytona: runners host multiple sandboxes and scale horizontally, while volumes provide persistent storage independently. [7]

---

# Phase 16 — Security hardening

Before real users:

## Sandbox isolation

Each sandbox must be isolated from:

```text
other users
other scopes
host
control-plane credentials
other sandbox processes
```

## Authorization

Every operation should resolve:

```text
authenticated principal
        |
        v
scope
        |
        v
permissions
        |
        v
sandbox capability
```

Never trust:

```text
scope_id
```

provided by the model.

## Credentials

Do not place broad cloud credentials inside the sandbox.

Prefer:

```text
short-lived capability
        |
        v
specific scope
        |
        v
specific operation
```

QM explicitly uses signed ingress/capability mechanisms and keeps authorization decisions in the core. [6]

---

# Phase 17 — Observability

Record every agent run:

```text
run_id
user_id
scope_id
sandbox_id
model
harness
started_at
finished_at
status
tokens
tool_calls
artifacts_created
error
```

Metrics:

```text
sandbox_start_latency
agent_run_latency
workspace_sync_latency
storage_bytes
storage_operations
active_sandboxes
failed_sandboxes
API_latency
Postgres_connections
```

Do not keep important state only in process memory. QM explicitly warns that multi-instance/blue-green deployments make in-memory state unsafe for anything that must survive deployment. [10]

---

# Phase 18 — Backups and disaster recovery

Back up:

```text
PostgreSQL
```

Object storage should use:

```text
versioning where appropriate
retention policy
lifecycle rules
```

Important distinction:

```text
Postgres backup
    = metadata recovery

Object storage durability
    = file recovery
```

You need both.

---

# Phase 19 — Production deployment workflow

Use:

```text
git push
   |
   v
CI
   |
   +-- tests
   +-- build API
   +-- build worker
   +-- build sandbox
   +-- security scan
   |
   v
container registry
   |
   v
GCP deployment
```

Use immutable image digests.

For database migrations:

```text
backup
  |
migration
  |
deploy
  |
health check
```

Have rollback procedures.

QM's deployment tooling is a useful reference here: it treats deployment configuration as a committed artifact, validates it, pins images, and has explicit deployment/rollback workflows. [4][9]

---

# Phase 20 — Final target architecture

```text
                              USER
                                |
                                v
                       ┌─────────────────┐
                       │ Web / API       │
                       └────────┬────────┘
                                |
                                v
                    ┌──────────────────────┐
                    │      Agent Core      │
                    │                      │
                    │ identity             │
                    │ scope                │
                    │ permissions          │
                    │ sessions             │
                    │ scheduler            │
                    └───────┬───────┬──────┘
                            |       |
                            |       |
                     metadata       |
                            |       |
                            v       v
                       PostgreSQL  Sandbox Manager
                                      |
                             +--------+--------+
                             |        |        |
                             v        v        v
                          Sandbox  Sandbox  Sandbox
                             |        |        |
                             +--------+--------+
                                      |
                                  /workspace
                                      |
                                      v
                               Workspace layer
                                      |
                                      v
                               Cloud Storage
```

---

# Development vs Production

| Concern | Development | Production |
|---|---|---|
| API | Docker | Cloud Run/GKE |
| PostgreSQL | Docker Postgres | Cloud SQL |
| Object storage | SeaweedFS | GCS |
| Sandbox | Docker | GKE/Compute/specialized sandbox |
| Workspace | local mounted/synced volume | GCS-backed persistent layer |
| Queue | Postgres/Redis | managed queue/Redis |
| Secrets | `.env` locally | Secret Manager |
| Load balancer | none/local | GCP Load Balancer |
| Logging | Docker logs | Cloud Logging |
| Monitoring | local | Cloud Monitoring |
| Images | local Docker | Artifact Registry |
| TLS | local | GCP-managed |
| Backups | local/dev | managed DB + object-storage policies |
| Concurrency test | 10/25/50 | autoscaling |
| Cost | near-zero software cost | pay-as-you-go |

---

# Implementation order

Do **not** build everything simultaneously.

Follow this order:

```text
1. ObjectStorage interface
        ↓
2. Local SeaweedFS
        ↓
3. PostgreSQL workspace metadata
        ↓
4. Workspace API
        ↓
5. Local Docker sandbox
        ↓
6. Mount /workspace
        ↓
7. Agent execution
        ↓
8. Artifact tracking
        ↓
9. Workspace persistence
        ↓
10. Durable sandbox lifecycle
        ↓
11. Multi-user authorization
        ↓
12. 10/25/50 concurrent load tests
        ↓
13. Containerize
        ↓
14. GCP infrastructure
        ↓
15. GCS adapter
        ↓
16. Production sandbox workers
        ↓
17. Observability/security
        ↓
18. Production deployment
```

---

# What NOT to build initially

Avoid these until the basic system works:

- Custom FUSE filesystem
- Distributed filesystem
- Kubernetes operator
- Custom scheduler
- Multi-region storage
- Custom object-storage implementation
- Complex file-versioning system
- Event-sourcing
- Vector database for workspace search
- Multiple sandbox providers
- GPU sandbox pool
- MicroVM orchestration from scratch

First prove:

```text
User
 ↓
Scope
 ↓
Sandbox
 ↓
Agent
 ↓
/workspace/report.md
 ↓
Persistent storage
 ↓
User sees report.md
```

Then scale it.

---

# Reference implementations to study

## 1. YC QM — highest priority

QM is directly relevant to your platform.

It explicitly provides each person/room with scoped files, memory, permissions and a durable sandbox. Its core is harness-agnostic and its sandbox/session/memory substrates are abstracted behind interfaces. [1]

Read:

- README
- deployment directory contract
- sandbox/deployment layer
- security model
- deployment CLI

References:
- https://github.com/yc-software/qm
- https://github.com/yc-software/qm/blob/main/docs/deploy-directory.md
- https://github.com/yc-software/qm/blob/main/SECURITY.md

## 2. Daytona — persistent sandbox + storage

Daytona is particularly useful for understanding the distinction between:

```text
sandbox lifecycle
vs
persistent volume lifecycle
```

Its volumes are S3-compatible-object-store-backed, survive sandbox deletion, can be mounted into multiple sandboxes, and support per-user/per-workspace subpaths. [2][7]

Reference:
- https://www.daytona.io/docs/en/architecture/
- https://www.daytona.io/docs/en/persistence/
- https://www.daytona.io/docs/en/volumes/

## 3. Vercel filesystem agents

Vercel demonstrates the simple agent model:

```text
LLM
 |
 +-- bash
 |
 +-- sandbox filesystem
```

This is useful for the agent-facing side of your workspace design. [3][5]

References:
- https://vercel.com/academy/filesystem-agents
- https://vercel.com/academy/filesystem-agents/wire-up-sandbox

## 4. Google Cloud

Use GCP documentation when implementing the production deployment:

- Cloud Run pricing: https://cloud.google.com/run/pricing
- Cloud SQL pricing: https://cloud.google.com/sql/pricing
- Cloud Storage documentation/pricing: https://cloud.google.com/storage

GCP pricing is usage/region dependent; use the official calculator before production sizing. [8][11]

---

# Final recommendation

For **your exact project**, I would implement the first version as:

```text
LOCAL

Docker Compose
├── FastAPI
├── PostgreSQL
├── SeaweedFS
├── Docker Sandbox
└── Frontend
```

with these two abstractions:

```text
ObjectStorage
    └── SeaweedFSStorage

Sandbox
    └── DockerSandbox
```

Then production becomes:

```text
GCP

Cloud Run / GKE
├── Agent Core
├── Workspace API
└── Scheduler

Cloud SQL
    └── PostgreSQL

GCS
    └── Workspace objects

GKE / Compute / sandbox provider
    └── isolated sandboxes
```

with:

```text
ObjectStorage
    └── GCSStorage

Sandbox
    └── GcpSandbox
```

The **Agent Core, Workspace model, APIs, database schema, artifact model, authorization model and frontend should remain essentially unchanged**.

That is the key design goal: **change infrastructure adapters, not your application architecture.**

---

## References

[1] YC QM — architecture, scoped workspaces, durable sandboxes, pluggable substrates.  
[2] Daytona — persistence model.  
[3] Vercel — filesystem-agent model.  
[4] YC QM — deployment directory and immutable sandbox/image deployment.  
[5] Vercel — loading files into a sandbox and exposing filesystem/bash to agents.  
[6] YC QM — security and authorization boundaries.  
[7] Daytona — S3-backed persistent volumes and multi-sandbox sharing.  
[8] Google Cloud — Cloud Run pricing and scaling model.  
[9] YC QM — deployment/rollback workflow.  
[10] YC QM — durability and multi-instance state.  
[11] Google Cloud — Cloud SQL pricing.
