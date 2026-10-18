# Production-Grade Agent Platform Architecture Guide

**Date:** 2026-08-26
**Purpose:** Architectural blueprint for building a full-stack agent platform where thousands of users can build, test, and deploy custom agents.

**Analysis Sources:**
- **Corridor Platform**: Backend architecture, frontend patterns, API design
- **Onyx**: Chat interface, workspace isolation, API key management, streaming
- **Prime-Agent**: Orchestration layer (session management, crash recovery)

---

## Table of Contents

1. [Technology Stack](#technology-stack)
2. [Backend Architecture](#backend-architecture)
3. [Frontend Architecture](#frontend-architecture)
4. [Chat/Testing Interface](#chattesting-interface)
5. [User Workspace Implementation](#user-workspace-implementation)
6. [API Key Management](#api-key-management)
7. [Complete Implementation Roadmap](#complete-implementation-roadmap)

---

## Technology Stack

### Backend

```python
# Core Framework
FastAPI 0.115+                  # High-performance ASGI framework
Python 3.11+                    # Modern Python with async/await

# Database & ORM
SQLAlchemy 2.0+ (async)         # ORM with async support
Alembic                         # Database migrations
PostgreSQL 15+                  # Primary database
asyncpg                         # Async PostgreSQL driver

# Task Queue
Celery 5.4+                     # Background task processing
Redis 7+                        # Celery broker + caching

# Authentication
passlib                         # Password hashing
pyjwt                           # JWT tokens
python3-saml                    # Enterprise SSO
authlib                         # OAuth2/OIDC

# File Storage
boto3                           # AWS S3
minio                           # S3-compatible (self-hosted)

# Observability
OpenTelemetry                   # Distributed tracing
Sentry                          # Error tracking
```

### Frontend

```javascript
// Core Framework
Next.js 16+ (App Router)        // React framework with SSR
React 19+                       // UI library
TypeScript 5.9+                 // Type safety

// UI Components
Tailwind CSS 4+                 // Utility-first CSS
Shadcn/ui OR PrimeNG           // Component library
Framer Motion 12+               // Animations

// State Management
Zustand 5+                      // Lightweight state management
SWR 2+                          // Data fetching + caching

// Code Editor
CodeMirror 6                    // In-browser code editing
Monaco Editor                   // Alternative (VS Code editor)

// Markdown
react-markdown 9+               // Markdown rendering
rehype-highlight                // Code syntax highlighting
rehype-katex                    // Math rendering
```

### Infrastructure

```yaml
# Container Orchestration
Docker + Docker Compose         # Local development
Kubernetes                      # Production (optional)

# Sandbox Isolation
Docker-in-Docker                # User code execution
E2B                             # Managed sandbox (alternative)

# Reverse Proxy
Nginx                           # Load balancing, static files
Traefik                         # Dynamic routing (alternative)

# Monitoring
Prometheus + Grafana            # Metrics
Loki                            # Logs
Jaeger                          # Distributed tracing
```

---

## Backend Architecture

### Project Structure

```
agent-platform/
├── apps/
│   ├── api/                           # Main API service
│   │   ├── agent_platform/
│   │   │   ├── web/                   # API routes
│   │   │   │   ├── routes/
│   │   │   │   │   ├── auth.py
│   │   │   │   │   ├── agents.py
│   │   │   │   │   ├── tools.py
│   │   │   │   │   ├── sessions.py
│   │   │   │   │   ├── secrets.py
│   │   │   │   │   └── workspaces.py
│   │   │   │   ├── dependencies/
│   │   │   │   │   ├── auth.py        # Auth dependency injection
│   │   │   │   │   └── db.py          # DB session management
│   │   │   │   └── factory.py         # FastAPI app factory
│   │   │   ├── services/              # Business logic layer
│   │   │   │   ├── agent_service.py
│   │   │   │   ├── tool_registry.py
│   │   │   │   ├── secrets_vault.py
│   │   │   │   ├── workspace_service.py
│   │   │   │   └── billing_service.py
│   │   │   ├── db/
│   │   │   │   ├── models/            # SQLAlchemy models
│   │   │   │   │   ├── base.py
│   │   │   │   │   ├── user.py
│   │   │   │   │   ├── agent.py
│   │   │   │   │   ├── tool.py
│   │   │   │   │   ├── session.py
│   │   │   │   │   └── secret.py
│   │   │   │   └── migrations/        # Alembic migrations
│   │   │   ├── tasks/                 # Celery background tasks
│   │   │   │   ├── tool_validation.py
│   │   │   │   └── agent_execution.py
│   │   │   ├── supervisor/            # Prime-Agent integration
│   │   │   │   ├── session_manager.py
│   │   │   │   ├── journal.py
│   │   │   │   └── sandbox.py
│   │   │   ├── security/              # Security utilities
│   │   │   │   ├── encryption.py
│   │   │   │   ├── code_validator.py  # AST analysis
│   │   │   │   └── sandbox_executor.py
│   │   │   ├── config/
│   │   │   │   ├── settings.py
│   │   │   │   └── logging.py
│   │   │   └── utils/
│   │   └── pyproject.toml
│   └── sandbox-proxy/                 # Credential injection sidecar
│       ├── credential_injector.py
│       ├── resolvers/
│       │   ├── openai.py
│       │   ├── anthropic.py
│       │   └── mcp.py
│       └── proxy.py
├── packages/
│   └── shared-types/                  # Shared Python types
└── docker-compose.yml
```

---

### Database Schema (SQLAlchemy Models)

#### Base Model Pattern

```python
# agent_platform/db/models/base.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, DateTime, func
from datetime import datetime

class Base(DeclarativeBase):
    pass

class BaseModel(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

class Auditable:
    """Mixin for audit fields"""
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

#### User Model

```python
# agent_platform/db/models/user.py
from sqlalchemy import String, LargeBinary, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import BaseModel, Auditable

class User(BaseModel, Auditable):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    plan: Mapped[str] = mapped_column(String(50), default="free")  # free|pro|enterprise

    # Relationships
    agents = relationship("Agent", back_populates="user")
    tools = relationship("Tool", back_populates="user")
    secrets = relationship("UserSecret", back_populates="user")
    sessions = relationship("Session", back_populates="user")
```

#### Agent Model

```python
# agent_platform/db/models/agent.py
from sqlalchemy import String, Text, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import BaseModel, Auditable

class Agent(BaseModel, Auditable):
    __tablename__ = "agents"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Agent configuration (stored as JSON)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    # {
    #   "system_prompt": "You are...",
    #   "model": "claude-3-5-sonnet",
    #   "tools": ["tool_id_1", "tool_id_2"],
    #   "subagents": [...],
    #   "sandbox": "docker"
    # }

    # Composition pins (immutable snapshot)
    composition_digest: Mapped[str | None] = mapped_column(String(64))  # sha256
    tool_set_digest: Mapped[str | None] = mapped_column(String(64))
    prompt_digest: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(50), default="active")  # active|archived

    # Relationships
    user = relationship("User", back_populates="agents")
    sessions = relationship("Session", back_populates="agent")
```

#### Tool Model

```python
# agent_platform/db/models/tool.py
from sqlalchemy import String, Text, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import BaseModel, Auditable

class Tool(BaseModel, Auditable):
    __tablename__ = "tools"
    __table_args__ = (
        Index("idx_tools_user_name", "user_id", "name"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Tool code (Python function)
    code: Mapped[str] = mapped_column(Text, nullable=False)

    # JSON Schema for parameters
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Dependencies
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    # e.g., ["pandas==2.0.0", "requests>=2.31.0"]

    # Versioning
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # sha256

    # Validation status
    status: Mapped[str] = mapped_column(String(50), default="draft")
    # draft|validating|approved|rejected|active|deprecated

    # Security classification
    tool_class: Mapped[str] = mapped_column(String(50), default="non_atomic")
    # pure|idempotent|transactional|non_atomic

    # Required secrets (names only)
    required_secrets: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Relationships
    user = relationship("User", back_populates="tools")
```

#### Session Model

```python
# agent_platform/db/models/session.py
from sqlalchemy import String, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import BaseModel
from datetime import datetime

class Session(BaseModel):
    __tablename__ = "sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)

    # Session state
    status: Mapped[str] = mapped_column(String(50), default="active")
    # active|running|completed|failed|terminated

    # Workspace path
    workspace_path: Mapped[str | None] = mapped_column(String(512))

    # Sandbox ID (if using external sandbox service)
    sandbox_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Cost tracking
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)
    total_tokens: Mapped[int] = mapped_column(default=0)

    # Session metadata
    metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    # Last activity
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    user = relationship("User", back_populates="sessions")
    agent = relationship("Agent", back_populates="sessions")
    events = relationship("Event", back_populates="session")
```

#### User Secret Model

```python
# agent_platform/db/models/secret.py
from sqlalchemy import String, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import BYTEA
from .base import BaseModel

class UserSecret(BaseModel):
    __tablename__ = "user_secrets"
    __table_args__ = (
        Index("idx_secrets_user_name", "user_id", "name", unique=True),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Encrypted value (AES-256)
    encrypted_value: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    # Relationships
    user = relationship("User", back_populates="secrets")
```

---

### API Routes (FastAPI)

#### Application Factory

```python
# agent_platform/web/factory.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting application...")
    yield
    # Shutdown
    print("Shutting down...")

def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Platform API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from .routes import auth, agents, tools, sessions, secrets, workspaces

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
    app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(secrets.router, prefix="/api/v1/secrets", tags=["secrets"])
    app.include_router(workspaces.router, prefix="/api/v1/workspaces", tags=["workspaces"])

    return app
```

#### Authentication Dependency

```python
# agent_platform/web/dependencies/auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from ..db.session import get_db
from ..db.models import User

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Extract and validate JWT token, return current user."""
    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"]
        )
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Fetch user from database
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    return user
```

#### Agent Routes

```python
# agent_platform/web/routes/agents.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from ..dependencies.auth import get_current_user
from ..db.session import get_db
from ..db.models import User, Agent
from ..services.agent_service import AgentService

router = APIRouter()

class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    system_prompt: str
    model: str
    tools: list[str]  # Tool IDs
    subagents: list[dict] = []
    sandbox: str = "docker"

class AgentResponse(BaseModel):
    id: int
    name: str
    description: str | None
    status: str
    created_at: str

    class Config:
        from_attributes = True

@router.post("/", response_model=AgentResponse)
async def create_agent(
    data: AgentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new agent."""
    service = AgentService(db)

    # Validate user quota
    if not await service.check_quota(user.id, "max_agents"):
        raise HTTPException(400, "Agent limit reached")

    # Validate tools exist and user owns them
    await service.validate_tools(user.id, data.tools)

    # Create agent
    agent = await service.create_agent(
        user_id=user.id,
        name=data.name,
        description=data.description,
        config={
            "system_prompt": data.system_prompt,
            "model": data.model,
            "tools": data.tools,
            "subagents": data.subagents,
            "sandbox": data.sandbox,
        }
    )

    return agent

@router.get("/", response_model=list[AgentResponse])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's agents."""
    service = AgentService(db)
    agents = await service.list_agents(user.id)
    return agents

@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get agent by ID."""
    service = AgentService(db)
    agent = await service.get_agent(user.id, agent_id)

    if agent is None:
        raise HTTPException(404, "Agent not found")

    return agent

@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an agent."""
    service = AgentService(db)
    await service.delete_agent(user.id, agent_id)
    return {"success": True}
```

#### Session Execution with SSE

```python
# agent_platform/web/routes/sessions.py
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
import json
import asyncio

router = APIRouter()

@router.post("/{session_id}/run")
async def run_agent_session(
    session_id: str,
    message: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Execute agent session and stream events via SSE."""

    # Validate session ownership
    session = await db.get(Session, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(404, "Session not found")

    async def event_generator():
        """Generate SSE events from agent execution."""

        # Start agent execution
        agent_runtime = await supervisor.create_runtime(session_id)

        # Subscribe to events
        async for event in agent_runtime.events():
            # Map internal events to SSE packets
            packet = map_event_to_packet(event)

            # Yield SSE event
            yield {
                "event": "message",
                "data": json.dumps(packet)
            }

            # Break on completion
            if event.type == "run_end":
                break

        # Send completion event
        yield {
            "event": "message",
            "data": json.dumps({"type": "stream_complete"})
        }

    return EventSourceResponse(event_generator())
```

---

### Services Layer

#### Agent Service (with Mixin Pattern)

```python
# agent_platform/services/agent_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..db.models import Agent, Tool
import hashlib
import json

class AgentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_quota(self, user_id: int, quota_type: str) -> bool:
        """Check if user can create more agents."""
        if quota_type == "max_agents":
            # Count user's agents
            result = await self.db.execute(
                select(func.count(Agent.id))
                .where(Agent.user_id == user_id, Agent.status == "active")
            )
            count = result.scalar()

            # Get user's plan limit
            user = await self.db.get(User, user_id)
            limits = {
                "free": 5,
                "pro": 50,
                "enterprise": 1000
            }

            return count < limits[user.plan]

        return True

    async def validate_tools(self, user_id: int, tool_ids: list[str]) -> None:
        """Verify all tools exist and user owns them."""
        result = await self.db.execute(
            select(Tool).where(
                Tool.id.in_(tool_ids),
                Tool.user_id == user_id,
                Tool.status == "active"
            )
        )
        tools = result.scalars().all()

        if len(tools) != len(tool_ids):
            raise ValueError("Some tools not found or not accessible")

    async def create_agent(
        self,
        user_id: int,
        name: str,
        description: str | None,
        config: dict
    ) -> Agent:
        """Create new agent with composition digest."""

        # Compute composition digest (content-addressed)
        digest = self._compute_digest(config)

        agent = Agent(
            user_id=user_id,
            name=name,
            description=description,
            config=config,
            composition_digest=digest,
            status="active",
            created_by=user_id
        )

        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)

        return agent

    def _compute_digest(self, config: dict) -> str:
        """Compute SHA256 digest of agent config."""
        canonical = json.dumps(config, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def list_agents(self, user_id: int) -> list[Agent]:
        """List user's agents."""
        result = await self.db.execute(
            select(Agent)
            .where(Agent.user_id == user_id, Agent.status == "active")
            .order_by(Agent.created_at.desc())
        )
        return result.scalars().all()

    async def get_agent(self, user_id: int, agent_id: int) -> Agent | None:
        """Get agent by ID (with ownership check)."""
        result = await self.db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def delete_agent(self, user_id: int, agent_id: int) -> None:
        """Soft-delete agent."""
        agent = await self.get_agent(user_id, agent_id)
        if agent is None:
            raise ValueError("Agent not found")

        agent.status = "archived"
        await self.db.commit()
```

#### Tool Registry Service

```python
# agent_platform/services/tool_registry.py
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import Tool
from ..tasks.tool_validation import validate_tool_code
import hashlib

class ToolRegistryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_tool(
        self,
        user_id: int,
        name: str,
        description: str,
        code: str,
        parameters: dict,
        requirements: list[str] = None
    ) -> Tool:
        """Create new tool and enqueue validation."""

        # Compute digest
        content = json.dumps({
            "code": code,
            "parameters": parameters
        }, sort_keys=True)
        digest = hashlib.sha256(content.encode()).hexdigest()

        # Check if already exists
        existing = await self._find_by_digest(user_id, digest)
        if existing:
            return existing  # Idempotent

        # Determine version
        version = await self._next_version(user_id, name)

        tool = Tool(
            user_id=user_id,
            name=name,
            description=description,
            code=code,
            parameters=parameters,
            requirements=requirements or [],
            version=version,
            digest=digest,
            status="draft",
            created_by=user_id
        )

        self.db.add(tool)
        await self.db.commit()
        await self.db.refresh(tool)

        # Enqueue validation
        validate_tool_code.delay(tool.id)

        return tool

    async def _next_version(self, user_id: int, name: str) -> str:
        """Compute next semantic version for tool."""
        result = await self.db.execute(
            select(Tool.version)
            .where(Tool.user_id == user_id, Tool.name == name)
            .order_by(Tool.version.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()

        if latest is None:
            return "1.0.0"

        # Parse and increment patch version
        major, minor, patch = map(int, latest.split("."))
        return f"{major}.{minor}.{patch + 1}"
```

#### Secrets Vault Service

```python
# agent_platform/services/secrets_vault.py
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import UserSecret
from ..config.settings import settings

class SecretsVaultService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.cipher = Fernet(settings.ENCRYPTION_KEY.encode())

    async def store(self, user_id: int, name: str, value: str) -> None:
        """Store encrypted secret."""
        encrypted = self.cipher.encrypt(value.encode())

        # Upsert
        existing = await self._get_secret(user_id, name)
        if existing:
            existing.encrypted_value = encrypted
        else:
            secret = UserSecret(
                user_id=user_id,
                name=name,
                encrypted_value=encrypted
            )
            self.db.add(secret)

        await self.db.commit()

    async def retrieve(self, user_id: int, name: str) -> str:
        """Retrieve and decrypt secret."""
        secret = await self._get_secret(user_id, name)
        if secret is None:
            raise ValueError(f"Secret '{name}' not found")

        decrypted = self.cipher.decrypt(secret.encrypted_value)
        return decrypted.decode()

    async def list_names(self, user_id: int) -> list[str]:
        """List secret names (NOT values)."""
        result = await self.db.execute(
            select(UserSecret.name)
            .where(UserSecret.user_id == user_id)
        )
        return [row[0] for row in result.all()]

    async def delete(self, user_id: int, name: str) -> None:
        """Delete secret."""
        secret = await self._get_secret(user_id, name)
        if secret:
            await self.db.delete(secret)
            await self.db.commit()

    async def _get_secret(self, user_id: int, name: str) -> UserSecret | None:
        result = await self.db.execute(
            select(UserSecret).where(
                UserSecret.user_id == user_id,
                UserSecret.name == name
            )
        )
        return result.scalar_one_or_none()
```

---

### Background Tasks (Celery)

```python
# agent_platform/tasks/tool_validation.py
from celery import shared_task
from ..db.session import SessionLocal
from ..db.models import Tool
from ..security.code_validator import CodeValidator

@shared_task
def validate_tool_code(tool_id: int):
    """Background task: Validate user's tool code."""

    with SessionLocal() as db:
        tool = db.get(Tool, tool_id)
        if tool is None:
            return

        # Update status
        tool.status = "validating"
        db.commit()

        try:
            # Step 1: AST validation
            validator = CodeValidator()
            ast_result = validator.validate_ast(tool.code)

            if not ast_result.safe:
                tool.status = "rejected"
                tool.metadata = {"errors": ast_result.errors}
                db.commit()
                return

            # Step 2: Test execution in sandbox
            # (Simplified - actual impl would use Docker)
            # test_result = execute_in_sandbox(tool.code)

            # Step 3: Approve
            tool.status = "approved"
            db.commit()

        except Exception as e:
            tool.status = "rejected"
            tool.metadata = {"error": str(e)}
            db.commit()
```

---

## Frontend Architecture

### Project Structure

```
agent-platform-ui/
├── src/
│   ├── app/                           # Next.js App Router
│   │   ├── (auth)/
│   │   │   ├── login/
│   │   │   └── register/
│   │   ├── (platform)/
│   │   │   ├── agents/
│   │   │   │   ├── page.tsx           # Agent list
│   │   │   │   ├── new/
│   │   │   │   │   └── page.tsx       # Agent builder
│   │   │   │   └── [id]/
│   │   │   │       ├── page.tsx       # Agent detail
│   │   │   │       └── test/
│   │   │   │           └── page.tsx   # Chat interface
│   │   │   ├── tools/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/
│   │   │   ├── sessions/
│   │   │   └── settings/
│   │   ├── api/                       # API routes (proxy)
│   │   │   └── stream/
│   │   │       └── route.ts           # SSE proxy
│   │   └── layout.tsx
│   ├── components/
│   │   ├── ui/                        # Shadcn components
│   │   │   ├── button.tsx
│   │   │   ├── input.tsx
│   │   │   ├── card.tsx
│   │   │   └── ...
│   │   ├── agent-builder/
│   │   │   ├── AgentForm.tsx
│   │   │   ├── ToolSelector.tsx
│   │   │   └── PromptEditor.tsx
│   │   ├── chat/
│   │   │   ├── ChatInterface.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── MessageInput.tsx
│   │   │   └── MessageRenderer.tsx
│   │   ├── workspace/
│   │   │   ├── FileExplorer.tsx
│   │   │   ├── FilePreview.tsx
│   │   │   └── CodeEditor.tsx
│   │   └── tool-builder/
│   │       ├── ToolForm.tsx
│   │       └── CodeEditor.tsx
│   ├── lib/
│   │   ├── api/                       # API client
│   │   │   ├── client.ts
│   │   │   ├── agents.ts
│   │   │   ├── tools.ts
│   │   │   └── sessions.ts
│   │   ├── streaming/
│   │   │   ├── sse.ts                 # SSE handler
│   │   │   └── packets.ts             # Packet types
│   │   ├── stores/
│   │   │   ├── sessionStore.ts        # Zustand store
│   │   │   └── workspaceStore.ts
│   │   └── utils/
│   │       ├── cn.ts
│   │       └── markdown.ts
│   └── types/
│       ├── agent.ts
│       ├── tool.ts
│       ├── session.ts
│       └── streaming.ts
├── public/
├── package.json
└── tsconfig.json
```

---

### State Management (Zustand)

```typescript
// lib/stores/sessionStore.ts
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  streamItems?: StreamItem[];
  timestamp: Date;
}

interface StreamItem {
  type: "text" | "tool_call" | "thinking";
  id: string;
  content?: string;
  toolCall?: ToolCallState;
  isStreaming?: boolean;
}

interface SessionData {
  id: string;
  agentId: string;
  status: "active" | "running" | "completed";
  messages: Message[];
  streamItems: StreamItem[];  // Current turn
  abortController: AbortController;
  queuedMessages: string[];
}

interface SessionStore {
  sessions: Map<string, SessionData>;
  currentSessionId: string | null;

  // Actions
  createSession: (agentId: string) => Promise<string>;
  setCurrentSession: (id: string) => void;
  appendMessage: (sessionId: string, message: Message) => void;
  appendStreamItem: (sessionId: string, item: StreamItem) => void;
  updateStreamItem: (sessionId: string, itemId: string, updates: Partial<StreamItem>) => void;
  finalizeStreaming: (sessionId: string) => void;
  abortSession: (sessionId: string) => void;
}

export const useSessionStore = create<SessionStore>()(
  immer((set, get) => ({
    sessions: new Map(),
    currentSessionId: null,

    createSession: async (agentId: string) => {
      const response = await fetch("/api/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agentId }),
      });

      const { session_id } = await response.json();

      set((state) => {
        state.sessions.set(session_id, {
          id: session_id,
          agentId,
          status: "active",
          messages: [],
          streamItems: [],
          abortController: new AbortController(),
          queuedMessages: [],
        });
        state.currentSessionId = session_id;
      });

      return session_id;
    },

    appendStreamItem: (sessionId, item) =>
      set((state) => {
        const session = state.sessions.get(sessionId);
        if (session) {
          session.streamItems.push(item);
        }
      }),

    updateStreamItem: (sessionId, itemId, updates) =>
      set((state) => {
        const session = state.sessions.get(sessionId);
        if (!session) return;

        const item = session.streamItems.find((i) => i.id === itemId);
        if (item) {
          Object.assign(item, updates);
        }
      }),

    finalizeStreaming: (sessionId) =>
      set((state) => {
        const session = state.sessions.get(sessionId);
        if (!session) return;

        // Convert stream items to message
        const textContent = session.streamItems
          .filter((i) => i.type === "text")
          .map((i) => i.content)
          .join("");

        session.messages.push({
          id: crypto.randomUUID(),
          role: "assistant",
          content: textContent,
          streamItems: [...session.streamItems],
          timestamp: new Date(),
        });

        session.streamItems = [];
        session.status = "active";
      }),

    abortSession: (sessionId) =>
      set((state) => {
        const session = state.sessions.get(sessionId);
        if (session) {
          session.abortController.abort();
          session.abortController = new AbortController();
          session.status = "active";
        }
      }),
  }))
);
```

---

### SSE Streaming Utility

```typescript
// lib/streaming/sse.ts
export interface SSEPacket {
  type: string;
  [key: string]: any;
}

export async function* handleSSEStream<T extends SSEPacket>(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<T, void, unknown> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  // Abort handling
  const abortHandler = () => {
    reader.cancel();
  };
  signal?.addEventListener("abort", abortHandler);

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      // Decode chunk and add to buffer
      buffer += decoder.decode(value, { stream: true });

      // Split by newlines
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.trim() === "") continue;

        try {
          const packet = JSON.parse(line) as T;

          // Filter heartbeats
          if (packet.type === "heartbeat") continue;

          yield packet;
        } catch (error) {
          console.error("Failed to parse SSE packet:", error, line);
        }
      }
    }
  } finally {
    signal?.removeEventListener("abort", abortHandler);
    reader.cancel().catch(() => {});
  }
}
```

---

### Packet Processor

```typescript
// lib/streaming/processor.ts
import { useSessionStore } from "../stores/sessionStore";
import { SSEPacket } from "./sse";

interface TextChunkPacket extends SSEPacket {
  type: "text_chunk";
  text: string;
}

interface ToolCallStartPacket extends SSEPacket {
  type: "tool_call_start";
  toolCallId: string;
  toolName: string;
  args: any;
}

interface ToolCallProgressPacket extends SSEPacket {
  type: "tool_call_progress";
  toolCallId: string;
  status: "running" | "completed" | "failed";
  title?: string;
  result?: any;
}

interface StreamCompletePacket extends SSEPacket {
  type: "stream_complete";
}

type Packet = TextChunkPacket | ToolCallStartPacket | ToolCallProgressPacket | StreamCompletePacket;

export function createPacketProcessor(sessionId: string) {
  const { appendStreamItem, updateStreamItem, finalizeStreaming } = useSessionStore.getState();

  let accumulatedText = "";
  let lastItemType: "text" | "tool" | null = null;

  return (packet: Packet) => {
    switch (packet.type) {
      case "text_chunk": {
        if (lastItemType === "text") {
          // Merge consecutive text chunks
          accumulatedText += packet.text;
          updateStreamItem(sessionId, "streaming-text", {
            content: accumulatedText,
          });
        } else {
          // Start new text item
          accumulatedText = packet.text;
          appendStreamItem(sessionId, {
            type: "text",
            id: "streaming-text",
            content: packet.text,
            isStreaming: true,
          });
          lastItemType = "text";
        }
        break;
      }

      case "tool_call_start": {
        accumulatedText = "";
        lastItemType = "tool";

        appendStreamItem(sessionId, {
          type: "tool_call",
          id: packet.toolCallId,
          toolCall: {
            id: packet.toolCallId,
            name: packet.toolName,
            args: packet.args,
            status: "running",
          },
        });
        break;
      }

      case "tool_call_progress": {
        updateStreamItem(sessionId, packet.toolCallId, {
          toolCall: {
            status: packet.status,
            title: packet.title,
            result: packet.result,
          },
        });
        break;
      }

      case "stream_complete": {
        // Finalize streaming text
        if (lastItemType === "text") {
          updateStreamItem(sessionId, "streaming-text", {
            isStreaming: false,
          });
        }

        finalizeStreaming(sessionId);
        break;
      }
    }
  };
}
```

---

## Chat/Testing Interface

### Chat Interface Component

```typescript
// components/chat/ChatInterface.tsx
"use client";

import { useEffect, useRef } from "react";
import { useSessionStore } from "@/lib/stores/sessionStore";
import { handleSSEStream } from "@/lib/streaming/sse";
import { createPacketProcessor } from "@/lib/streaming/processor";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { Button } from "@/components/ui/button";

interface ChatInterfaceProps {
  agentId: string;
}

export function ChatInterface({ agentId }: ChatInterfaceProps) {
  const {
    currentSessionId,
    sessions,
    createSession,
    appendMessage,
    abortSession,
  } = useSessionStore();

  const processorRef = useRef<ReturnType<typeof createPacketProcessor> | null>(null);

  useEffect(() => {
    // Create session on mount
    createSession(agentId);
  }, [agentId, createSession]);

  const session = currentSessionId ? sessions.get(currentSessionId) : null;

  const handleSendMessage = async (message: string) => {
    if (!session) return;

    // Add user message
    appendMessage(session.id, {
      id: crypto.randomUUID(),
      role: "user",
      content: message,
      timestamp: new Date(),
    });

    // Create packet processor
    processorRef.current = createPacketProcessor(session.id);

    try {
      // Start streaming
      const response = await fetch(`/api/v1/sessions/${session.id}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
        signal: session.abortController.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      // Process SSE stream
      for await (const packet of handleSSEStream(response, session.abortController.signal)) {
        processorRef.current(packet);
      }
    } catch (error) {
      if (error.name === "AbortError") {
        console.log("Stream aborted");
      } else {
        console.error("Streaming error:", error);
      }
    }
  };

  const handleAbort = () => {
    if (session) {
      abortSession(session.id);
    }
  };

  if (!session) {
    return <div>Loading...</div>;
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="border-b p-4 flex justify-between items-center">
        <h1 className="text-xl font-semibold">Test Agent</h1>
        {session.status === "running" && (
          <Button variant="destructive" onClick={handleAbort}>
            Stop
          </Button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4">
        <MessageList messages={session.messages} streamItems={session.streamItems} />
      </div>

      {/* Input */}
      <MessageInput
        onSend={handleSendMessage}
        disabled={session.status === "running"}
      />
    </div>
  );
}
```

### Message Renderer

```typescript
// components/chat/MessageRenderer.tsx
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";

interface MessageRendererProps {
  content: string;
}

export function MessageRenderer({ content }: MessageRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={{
        code({ node, inline, className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");

          return !inline && match ? (
            <SyntaxHighlighter
              style={vscDarkPlus}
              language={match[1]}
              PreTag="div"
              {...props}
            >
              {String(children).replace(/\n$/, "")}
            </SyntaxHighlighter>
          ) : (
            <code className="bg-gray-100 rounded px-1" {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
```

---

## User Workspace Implementation

### Workspace Architecture

**Per-User Sandbox with Per-Session Workspaces:**

```
/workspaces/
├── user_123/                      # User's sandbox container
│   ├── managed/
│   │   ├── skills/                # Platform-provided tools
│   │   └── user_library/          # User's shared code
│   └── sessions/
│       ├── session_abc/           # Session workspace
│       │   ├── outputs/
│       │   │   ├── data.json
│       │   │   ├── report.pdf
│       │   │   └── webapp/        # Next.js app
│       │   ├── attachments/       # Uploaded files
│       │   ├── venv/              # Python environment
│       │   ├── .opencode/
│       │   │   └── skills -> ../../managed/skills
│       │   └── AGENTS.md          # Agent instructions
│       └── session_def/
│           └── ...
└── user_456/
    └── ...
```

### Workspace Setup Script

```python
# agent_platform/services/workspace_service.py
import shlex
from pathlib import Path

class WorkspaceService:
    WORKSPACE_ROOT = Path("/workspaces")

    async def setup_session_workspace(
        self,
        user_id: str,
        session_id: str,
        agent_instructions: str
    ) -> Path:
        """Create per-session workspace inside user's sandbox."""

        user_root = self.WORKSPACE_ROOT / f"user_{user_id}"
        session_path = user_root / "sessions" / session_id

        # Build setup script
        script = self._build_setup_script(
            session_path=str(session_path),
            instructions=agent_instructions
        )

        # Execute in user's sandbox (via Docker exec or K8s exec)
        await self.execute_in_sandbox(user_id, script)

        return session_path

    def _build_setup_script(self, session_path: str, instructions: str) -> str:
        """Generate bash script for workspace setup (replay-safe)."""

        return f"""
set -e

# Lock-based serialization
(
flock -x 8

echo "Setting up workspace: {session_path}"

# Create directories
mkdir -p {session_path}/outputs
mkdir -p {session_path}/attachments
mkdir -p {session_path}/.opencode

# Symlink managed resources
ln -sfn /workspaces/managed/skills {session_path}/.opencode/skills
ln -sfn /workspaces/managed/user_library {session_path}/user_library

# Write agent instructions
printf '%s' {shlex.quote(instructions)} > {session_path}/AGENTS.md

# Mark complete
touch {session_path}/.setup-complete

echo "Workspace setup complete"

) 8>{session_path}.lock

echo "WORKSPACE_SETUP_COMPLETE"
"""
```

### File Operations API

```python
# agent_platform/web/routes/workspaces.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
import aiofiles

router = APIRouter()

@router.post("/sessions/{session_id}/files")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload file to session workspace."""

    # Validate session ownership
    session = await validate_session_ownership(db, user.id, session_id)

    # Validate file size
    if file.size > 50 * 1024 * 1024:  # 50MB
        raise HTTPException(400, "File too large")

    # Write to workspace
    workspace = WorkspaceService()
    file_path = await workspace.write_file(
        user_id=user.id,
        session_id=session_id,
        filename=file.filename,
        content=await file.read()
    )

    return {
        "path": str(file_path),
        "name": file.filename,
        "size": file.size
    }

@router.get("/sessions/{session_id}/files/{file_path:path}")
async def download_file(
    session_id: str,
    file_path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Download file from session workspace."""

    session = await validate_session_ownership(db, user.id, session_id)

    workspace = WorkspaceService()
    full_path = await workspace.get_file_path(user.id, session_id, file_path)

    if not full_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(full_path)

@router.get("/sessions/{session_id}/files")
async def list_files(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List files in session workspace."""

    session = await validate_session_ownership(db, user.id, session_id)

    workspace = WorkspaceService()
    files = await workspace.list_files(user.id, session_id)

    return {"files": files}
```

### Frontend File Explorer

```typescript
// components/workspace/FileExplorer.tsx
"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { FileIcon, FolderIcon } from "lucide-react";

interface File {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
}

interface FileExplorerProps {
  sessionId: string;
}

export function FileExplorer({ sessionId }: FileExplorerProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    fetchFiles();
  }, [sessionId]);

  const fetchFiles = async () => {
    const response = await fetch(`/api/v1/workspaces/sessions/${sessionId}/files`);
    const data = await response.json();
    setFiles(data.files);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    await fetch(`/api/v1/workspaces/sessions/${sessionId}/files`, {
      method: "POST",
      body: formData,
    });

    fetchFiles();
  };

  const handleDownload = async (path: string) => {
    const response = await fetch(
      `/api/v1/workspaces/sessions/${sessionId}/files/${path}`
    );
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop() || "download";
    a.click();
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b">
        <input
          type="file"
          onChange={handleUpload}
          className="hidden"
          id="file-upload"
        />
        <label htmlFor="file-upload">
          <Button as="span">Upload File</Button>
        </label>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {files.map((file) => (
          <div
            key={file.path}
            className="flex items-center gap-2 p-2 hover:bg-gray-100 rounded cursor-pointer"
            onClick={() => {
              setSelectedFile(file.path);
              if (file.type === "file") {
                handleDownload(file.path);
              }
            }}
          >
            {file.type === "directory" ? (
              <FolderIcon className="w-4 h-4" />
            ) : (
              <FileIcon className="w-4 h-4" />
            )}
            <span>{file.name}</span>
            {file.size && (
              <span className="text-sm text-gray-500">
                ({(file.size / 1024).toFixed(1)} KB)
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## API Key Management

### Encrypted Storage

```python
# agent_platform/db/models/llm_provider.py
from sqlalchemy import String, JSON, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import BYTEA
from .base import BaseModel

class LLMProvider(BaseModel):
    __tablename__ = "llm_providers"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # "openai", "anthropic", "google", "bedrock", etc.

    # Encrypted API key
    encrypted_api_key: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    # Optional configurations
    api_base: Mapped[str | None] = mapped_column(String(512))
    api_version: Mapped[str | None] = mapped_column(String(50))

    # Model configurations
    default_model: Mapped[str] = mapped_column(String(255))
    available_models: Mapped[list[str]] = mapped_column(JSON, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="llm_providers")
```

### Credential Injection Proxy

```python
# apps/sandbox-proxy/credential_injector.py
from mitmproxy import http
from typing import Protocol
import logging

logger = logging.getLogger(__name__)

class CredentialResolver(Protocol):
    """Protocol for credential resolvers."""

    def claims(self, request: http.Request, context: dict) -> bool:
        """Check if this resolver handles this request."""
        ...

    def resolve(self, request: http.Request, context: dict) -> dict[str, str]:
        """Return auth headers to inject. Raise CredentialUnavailableError to block."""
        ...

class CredentialUnavailableError(Exception):
    def __init__(self, message: str, sandbox_detail: str):
        self.message = message
        self.sandbox_detail = sandbox_detail

class CredentialInjectionDispatcher:
    """First-claim-wins dispatcher for credential injection."""

    def __init__(self, resolvers: list[CredentialResolver]):
        self._resolvers = resolvers

    def inject(self, flow: http.HTTPFlow, context: dict) -> bool:
        """Inject credentials into request. Returns True if injected."""

        request = flow.request

        # Find resolver
        resolver = None
        for r in self._resolvers:
            if r.claims(request, context):
                resolver = r
                break

        if resolver is None:
            return False  # Pass-through

        try:
            headers = resolver.resolve(request, context)
        except CredentialUnavailableError as e:
            # Block request
            flow.response = http.Response.make(
                403,
                e.sandbox_detail,
                {"Content-Type": "text/plain"}
            )
            logger.warning(f"Credential unavailable: {e.message}")
            return True
        except Exception as e:
            # Fail closed
            flow.response = http.Response.make(
                500,
                "Credential injection error",
                {"Content-Type": "text/plain"}
            )
            logger.exception("Credential resolution failed")
            return True

        if not headers:
            return True  # Claimed but no injection

        # Inject headers
        for name, value in headers.items():
            flow.request.headers[name] = value

        logger.info(
            f"Injected credentials: resolver={type(resolver).__name__}, "
            f"host={request.host}, headers={','.join(headers.keys())}"
        )

        return True

# Example resolver
class OpenAIResolver:
    def __init__(self, secrets_service):
        self.secrets = secrets_service

    def claims(self, request: http.Request, context: dict) -> bool:
        return request.host == "api.openai.com"

    def resolve(self, request: http.Request, context: dict) -> dict[str, str]:
        user_id = context["user_id"]

        try:
            api_key = self.secrets.retrieve(user_id, "OPENAI_API_KEY")
        except ValueError:
            raise CredentialUnavailableError(
                f"User {user_id} missing OPENAI_API_KEY",
                sandbox_detail="Please configure your OpenAI API key in settings."
            )

        return {"Authorization": f"Bearer {api_key}"}
```

---

## Complete Implementation Roadmap

### Phase 1: Foundation (8-10 weeks)

**Weeks 1-2: Database & Auth**
- [ ] Set up PostgreSQL with SQLAlchemy models
- [ ] Implement User, Agent, Tool, Session models
- [ ] JWT authentication with refresh tokens
- [ ] User registration and login endpoints

**Weeks 3-4: Core API**
- [ ] Agent CRUD endpoints
- [ ] Tool CRUD endpoints
- [ ] User secrets encrypted storage
- [ ] Basic quota enforcement

**Weeks 5-6: Prime-Agent Integration**
- [ ] Integrate prime-agent supervisor
- [ ] Session creation and management
- [ ] Event journaling to database
- [ ] Basic sandbox support (local_process)

**Weeks 7-8: Frontend Foundation**
- [ ] Next.js project setup with TypeScript
- [ ] Authentication flow (login, register)
- [ ] Agent list and create pages
- [ ] Tool list and create pages

**Weeks 9-10: Testing & Polish**
- [ ] API integration tests
- [ ] Frontend E2E tests
- [ ] Error handling and validation
- [ ] Documentation

**Deliverable:** Users can register, create agents with pre-approved tools, and view their agents.

---

### Phase 2: Chat Interface & Execution (6-8 weeks)

**Weeks 11-12: SSE Streaming**
- [ ] Implement SSE endpoint for agent execution
- [ ] Packet processor for text, tools, errors
- [ ] Stream state management with Zustand
- [ ] Abort/cancellation support

**Weeks 13-14: Chat UI**
- [ ] Chat interface component
- [ ] Message list with markdown rendering
- [ ] Syntax highlighting for code blocks
- [ ] Tool call timeline visualization

**Weeks 15-16: Workspace Basics**
- [ ] File upload/download API
- [ ] File explorer component
- [ ] File preview for text/images
- [ ] Code editor integration (CodeMirror)

**Weeks 17-18: Testing & Optimization**
- [ ] Performance optimization (text chunk merging)
- [ ] Stream resume after disconnect
- [ ] Error recovery and retries
- [ ] User testing and feedback

**Deliverable:** Users can test their agents in a chat interface, upload files, and view workspace files.

---

### Phase 3: Security & Tool Validation (6-8 weeks)

**Weeks 19-20: Code Validation**
- [ ] AST parser for Python code
- [ ] Banned imports/patterns detection
- [ ] Complexity analysis (loops, recursion)
- [ ] Celery task for async validation

**Weeks 21-22: Sandboxed Execution**
- [ ] Docker-based sandbox for tool execution
- [ ] Resource limits (CPU, memory, time)
- [ ] Network isolation
- [ ] Tool execution logging

**Weeks 23-24: Credential Injection**
- [ ] Sandbox proxy (mitmproxy)
- [ ] Credential resolver pattern
- [ ] OpenAI, Anthropic resolvers
- [ ] Fail-closed security model

**Weeks 25-26: Tool Dependencies**
- [ ] requirements.txt parsing
- [ ] Virtualenv creation per tool
- [ ] Package security scanning
- [ ] Environment caching

**Deliverable:** Users can write custom tool code securely, tools are validated and sandboxed, secrets are injected at runtime.

---

### Phase 4: Production Features (8-10 weeks)

**Weeks 27-29: Observability**
- [ ] User logs API (filter by session, time)
- [ ] Metrics aggregation (cost, tokens, errors)
- [ ] Usage dashboard component
- [ ] Real-time cost tracking

**Weeks 30-33: Billing**
- [ ] Stripe integration
- [ ] Monthly usage aggregation
- [ ] Invoice generation
- [ ] Budget alerts and auto-pause

**Weeks 34-36: Advanced Features**
- [ ] Agent versioning and forking
- [ ] Shared tool marketplace
- [ ] Team collaboration (shared agents)
- [ ] WebApp preview (Next.js apps in workspace)

**Weeks 37-38: Polish & Launch**
- [ ] Performance optimization
- [ ] Security audit
- [ ] Documentation and tutorials
- [ ] Beta launch

**Deliverable:** Full production platform with billing, observability, and advanced features.

---

## Technology Decisions Summary

| Component | Technology | Reasoning |
|-----------|------------|-----------|
| **Backend Framework** | FastAPI | High performance, async support, auto-docs, type hints |
| **Database** | PostgreSQL | ACID, JSON support, full-text search, mature ecosystem |
| **ORM** | SQLAlchemy 2.0 (async) | Industry standard, async support, complex queries |
| **Task Queue** | Celery + Redis | Battle-tested, distributed, monitoring tools |
| **Frontend Framework** | Next.js 16 (App Router) | SSR, SEO, file-based routing, server components |
| **State Management** | Zustand | Lightweight, no boilerplate, great TypeScript support |
| **Data Fetching** | SWR | Auto-refresh, cache management, optimistic updates |
| **UI Components** | Shadcn/ui | Modern, accessible, customizable, Tailwind-based |
| **Streaming** | SSE (Server-Sent Events) | Simpler than WebSocket, auto-reconnect, HTTP/2 multiplexing |
| **Sandbox** | Docker | Industry standard, resource limits, network isolation |
| **Code Editor** | CodeMirror 6 | Modern, extensible, Python/JSON support |
| **File Storage** | S3-compatible (MinIO) | Scalable, durable, self-hosted option available |
| **Secrets** | Fernet (cryptography) | Simple, secure, industry-standard encryption |
| **Monitoring** | OpenTelemetry | Vendor-neutral, distributed tracing, metrics |

---

## Key Architectural Patterns

### 1. **Mixin Pattern (Backend Services)**
Compose service capabilities without deep inheritance hierarchies.

### 2. **Factory Pattern (App Initialization)**
Centralized app creation with middleware stack and router registration.

### 3. **Dependency Injection (FastAPI)**
Auth, DB sessions, and services injected via `Depends()`.

### 4. **Protocol-Based Contracts (Prime-Agent)**
Type-safe contracts between supervisor and providers (tools, sandbox, etc.).

### 5. **Content-Addressed Immutability**
Agent configurations pinned by SHA256 digest - prevents version drift.

### 6. **Event Sourcing (Journal)**
All agent actions journaled as events - enables crash recovery and audit trail.

### 7. **SSE with Packet Processing**
Text chunk merging, abort signals, heartbeat filtering for efficient streaming.

### 8. **Zustand Immer Middleware**
Immutable state updates with draft mutations for complex nested state.

### 9. **Credential Injection Proxy**
Zero-trust model - secrets never stored in sandbox, injected per-request.

### 10. **Registry Pattern (File Previews)**
Extensible routing based on file type - new formats added without core changes.

---

## Security Checklist

- [ ] **Authentication**: JWT with short-lived access tokens + refresh tokens
- [ ] **Authorization**: Row-level security (user_id on all queries)
- [ ] **Secrets**: Encrypted at rest (Fernet), decrypted only at use
- [ ] **Tool Code**: AST validation + sandboxed execution + resource limits
- [ ] **Sandbox**: Network isolation + no direct secret access
- [ ] **API**: Rate limiting + CORS + CSRF protection
- [ ] **Database**: Prepared statements (SQLAlchemy prevents SQL injection)
- [ ] **File Upload**: Size limits + type validation + virus scanning (optional)
- [ ] **Logs**: No secrets in logs (mask sensitive data)
- [ ] **Dependencies**: Security scanning (safety, snyk)

---

## Next Steps

1. **Clone this architecture guide** and share with your development team
2. **Set up development environment** (Docker Compose with PostgreSQL, Redis)
3. **Start Phase 1** (Database schema + Auth + Core API)
4. **Iterate based on user feedback** after each phase
5. **Scale infrastructure** as user base grows (K8s, managed DB, CDN)

---

**Document Version:** 1.0
**Last Updated:** 2026-08-26
**Maintainer:** Development Team
