# Database Architecture

The `db/` folder provides async database infrastructure with production-ready connection pooling.

---

## File Structure

```
db/
├── __init__.py     → Public exports
├── engine.py       → Engine creation, pooling, health checks
├── models.py       → SQLAlchemy ORM models
└── DB_ARCHITECTURE.md
```

---

## Configuration

All settings via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///:memory:` | Connection string |
| `DB_POOL_SIZE` | 20 (postgres) / 5 (sqlite) | Base pool connections |
| `DB_MAX_OVERFLOW` | 10 | Extra connections under load |
| `DB_POOL_RECYCLE` | 3600 | Seconds before recycling stale connections |
| `DB_POOL_PRE_PING` | true | Check connection health before use |
| `DB_ECHO` | false | Log SQL statements |

---

## Connection Pooling

Tuned for 100+ concurrent users:

```
┌─────────────────────────────────────────────────────────────┐
│                    Connection Pool                           │
│                                                              │
│   pool_size = 20      Base connections always kept open     │
│   max_overflow = 10   Extra connections when pool exhausted │
│   pool_recycle = 3600 Recycle after 1 hour (stale cleanup)  │
│   pool_pre_ping = ✓   Health check before each use          │
│                                                              │
│   Total capacity: 30 concurrent connections                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Models

12 SQLAlchemy models organized by domain:

### Session Domain
| Model | Table | Purpose |
|-------|-------|---------|
| `SessionRow` | `sessions` | Session metadata, composition pins |
| `EventRow` | `events` | Append-only event journal |
| `SessionIndexRow` | `session_index` | Session summaries for listing |
| `CommandRow` | `commands` | HTTP command deduplication |

### Catalog Domain
| Model | Table | Purpose |
|-------|-------|---------|
| `HarnessVersionRow` | `harness_versions` | Published harness manifests |
| `CatalogArtifactRow` | `catalog_artifacts` | PromptPack, ToolSet, Composition |

### Policy Domain
| Model | Table | Purpose |
|-------|-------|---------|
| `ApprovalRow` | `approvals` | Human-in-the-loop requests |
| `ToolOutboxRow` | `tool_outbox` | Tool invoke exactly-once outbox |

### Workspace Domain (Track B)
| Model | Table | Purpose |
|-------|-------|---------|
| `ScopeRow` | `scopes` | Workspace scopes (personal/project) |
| `WorkspaceFileRow` | `workspace_files` | File metadata (bytes in ObjectStorage) |
| `ScopeMembershipRow` | `scope_memberships` | Principal → Scope membership |
| `ScopeGrantRow` | `scope_grants` | Principal → Scope → Grant |

---

## Usage

### Basic Usage

```python
from supervisor.db import open_db, SessionRow

async with open_db() as (engine, db):
    async with db() as session:
        row = SessionRow(
            id="sess-123",
            tenant_id="acme",
            runtime="githi",
        )
        session.add(row)
        await session.commit()
```

### Health Check

```python
from supervisor.db import make_engine, check_health, get_pool_status

engine = make_engine()

# Simple health check
is_healthy = await check_health(engine)

# Pool metrics
status = await get_pool_status(engine)
# {"size": 20, "checked_in": 18, "checked_out": 2, "overflow": 0}
```

### Production Setup

```python
from supervisor.db import make_engine, session_factory, create_all

# Create engine (reads DATABASE_URL from env)
engine = make_engine()

# Create tables
await create_all(engine)

# Create session factory
db = session_factory(engine)

# Use in request handlers
async with db() as session:
    ...
```

---

## Key Design Decisions

1. **Async-only**: All database operations are async (SQLAlchemy 2.0 + asyncpg/aiosqlite)

2. **expire_on_commit=False**: Objects remain usable after commit without explicit refresh

3. **Connection pooling**: Pre-configured for production load (not hardcoded in each service)

4. **Health checks**: `check_health()` and `get_pool_status()` for observability

5. **Environment-driven**: All tuning via env vars, not code changes

---

## Indexes

Key indexes for query performance:

| Table | Index | Columns |
|-------|-------|---------|
| `events` | `uq_events_session_seq` | `(session_id, seq)` unique |
| `sessions` | `ix_sessions_scope_id` | `scope_id` |
| `workspace_files` | `uq_workspace_files_scope_path_live` | `(scope_id, path)` where not deleted |
| `scope_memberships` | `uq_scope_memberships_scope_principal` | `(scope_id, principal_id)` unique |
| `scope_grants` | `uq_scope_grants_scope_principal_grant` | `(scope_id, principal_id, grant)` unique |
