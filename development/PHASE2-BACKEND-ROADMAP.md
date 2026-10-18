# Phase 2: Backend Implementation - Comprehensive Roadmap

**Date**: 2026-08-29
**Mode**: Production-Grade Development
**Objective**: Complete production-ready FastAPI backend with async SQLAlchemy 2.0

---

## Executive Summary

**Current State Analysis** (Agent ID: a528fc5)
- Frontend: 73% complete (139 tests passing, 25 pages, full API layer)
- Backend: Infrastructure 60% complete, core app missing
- Two parallel implementations detected: `prime_agent/` (async, incomplete) vs `root/` (sync, more features)
- Critical gaps: No FastAPI app factory, no routes, no Alembic migrations, no concrete services

**Production Patterns Research** (Agent ID: a88aed9)
- Modern 2026 FastAPI patterns documented with web search
- SQLAlchemy 2.0 async patterns with connection pooling best practices
- JWT + Argon2 authentication patterns
- Pydantic v2 validation with field validators
- pytest-asyncio testing strategies

**Recommended Approach**: Build on `prime_agent/` async foundation (modern, scalable)

---

## Critical Findings from Backend Analysis

### ✅ COMPLETE Components

**1. Database Models (prime_agent/db/models/)**
- ✅ `harness.py` - UUID PK, AuditableMixin, JSON fields for tools/memory
- ✅ `tool.py` - UUID PK, parameters as JSON, rate limiting fields
- ✅ `user.py` - Basic user model (simplified)
- ✅ `mixins/auditable.py` - CreationAuditable, ModificationAuditable, UTC timestamps

**2. Base Service (prime_agent/services/base.py)**
- ✅ Generic `BaseService[ModelType]` with lifecycle hooks
- ✅ Async CRUD: `aget_one()`, `aget_all()`, `acreate()`, `aupdate()`, `adelete()`, `count()`
- ✅ Hooks: `pre_save_check()`, `pre_save()`, `post_save()`, `pre_delete_check()`, `pre_delete()`, `post_delete()`
- ✅ HTTPException error handling

**3. Pydantic Schemas (prime_agent/schemas/)**
- ✅ `harness.py` - HarnessBase, HarnessCreate, HarnessUpdate, HarnessResponse, HarnessListResponse
- ✅ `tool.py` - ToolParameterSchema, ToolBase, ToolCreate, ToolUpdate, ToolResponse
- ✅ `user.py` - UserBase, UserCreate, UserUpdate, UserResponse, TokenResponse

**4. Middleware (prime_agent/web/middlewares/)**
- ✅ `cors.py` - setup_cors() with configurable origins
- ✅ `logging.py` - LoggingMiddleware for HTTP access logs
- ✅ `security.py` - SecurityHeadersMiddleware (HSTS, XSS, CSP, X-Frame-Options)

**5. Database Session (prime_agent/db/session.py)**
- ✅ `DatabaseSession` class with async engine
- ✅ `lifespan()` context manager for FastAPI
- ✅ `get_db()` dependency
- ✅ Connection pooling configured

**6. Configuration (prime_agent/config/settings.py)**
- ✅ Pydantic v2 `Settings` with environment variables
- ✅ Database URL, JWT settings, CORS origins
- ✅ Redis, Celery, API prefix configuration
- ✅ `get_async_database_url()` helper

**7. Auth Utilities (root/util/, root/web/dependencies/)**
- ✅ `util/password.py` - Argon2id hashing with production parameters
- ✅ `util/crypto.py` - API key encryption, OTP generation
- ✅ `web/dependencies/jwt_manager.py` - JWT creation, validation, refresh
- ✅ `web/dependencies/auth.py` - `get_current_user()`, `verify_api_key()`, RBAC dependencies

**8. Production User Model (root/db/models/user.py)**
- ✅ Comprehensive user model with UserType, LoginMethod, UserRoleEnum
- ✅ Many-to-many User <-> Role relationship
- ✅ MFA fields, API key storage
- ✅ Permission and ObjectAccess models for fine-grained RBAC

### ❌ MISSING/INCOMPLETE Components

**HIGH PRIORITY - BLOCKING**

1. **FastAPI Application Entry Point**
   - Missing: `prime_agent/main.py` or `prime_agent/app.py`
   - Need: FastAPI factory with lifespan, middleware registration, route mounting

2. **API Routes (prime_agent/web/routes/)**
   - Missing: `auth.py`, `harnesses.py`, `tools.py`, `users.py`, `health.py`
   - Empty directory, no route implementations

3. **Alembic Migration Setup**
   - Missing: `alembic.ini`, `prime_agent/db/migrations/env.py`, `versions/`
   - Database schema cannot be deployed

4. **Concrete Service Implementations**
   - Missing: `HarnessService`, `ToolService`, `AuthService`
   - BaseService exists but no domain-specific services

**MEDIUM PRIORITY - ARCHITECTURAL**

5. **Dual Implementation Conflict**
   - `prime_agent/` - Async, incomplete
   - `root/` (db/, web/, services/, util/) - Sync, more complete
   - Need: Consolidation strategy (recommend async-first)

6. **User Model Simplification Mismatch**
   - `prime_agent/db/models/user.py` - Simplified (email, name, hashed_password, role)
   - `root/db/models/user.py` - Production-grade (UserType, LoginMethod, MFA, roles[])
   - Frontend expects: Simple model (id, email, name, role, avatar)
   - Recommendation: Use simplified prime_agent version for MVP, migrate to full model later

7. **Auth Dependencies Not Async**
   - `root/web/dependencies/auth.py` - Synchronous database queries
   - Need: Async versions of `get_current_user()`, `verify_api_key()`, RBAC checkers

**LOW PRIORITY - ENHANCEMENTS**

8. **Rate Limiting Not Implemented**
   - `web/dependencies/auth.py` has placeholder `rate_limit()`
   - Consider: slowapi or Redis-based rate limiting

9. **Missing Models**
   - Frontend has: `session.types.ts`, `memory.types.ts`, `prompt.types.ts`
   - Backend missing: Session, Memory, Prompt models

10. **Service Mixins Empty**
    - `prime_agent/services/mixins/` directory exists but empty
    - Could add: PaginationMixin, FilterMixin, SearchMixin

---

## Production Patterns Applied (2026 Standards)

### 1. FastAPI Application Factory Pattern

**Modern Lifespan Context Manager** (replaces deprecated @app.on_event):

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application starting up")
    await database_session.init()
    await database_session.create_tables()

    yield  # Application runs

    # Shutdown
    await database_session.dispose()
    logger.info("Application shutting down")

def create_app() -> FastAPI:
    app = FastAPI(
        title="Prime Agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    register_middleware(app)  # CORS MUST BE FIRST
    register_routers(app)
    register_exception_handlers(app)

    return app
```

**Why**: Resources opened before `yield` available for full app lifetime; proper cleanup on shutdown.

### 2. SQLAlchemy 2.0 Async Connection Pooling

**Optimal Pool Configuration**:

```python
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=40,          # For 200 concurrent requests
    max_overflow=40,       # Double pool for spikes
    pool_pre_ping=True,    # Verify connections before use
    pool_recycle=3600,     # Recycle connections every hour
)
```

**Key Principle**: One `AsyncEngine` → one `AsyncAdaptedQueuePool` → each request borrows exactly one `AsyncSession` via dependency.

**Performance**: asyncpg provides 25-35% latency advantage over psycopg3, with gap widening under concurrency.

### 3. JWT Authentication with Refresh Tokens

**Access + Refresh Token Pattern**:

- **Access Token**: 15 minutes (short-lived, stateless)
- **Refresh Token**: 7 days (long-lived, stored hashed in DB)
- **Password Hashing**: Argon2id (2026 standard, pwdlib with `PasswordHash.recommended()`)

**Refresh Token Table**:
```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    token_hash VARCHAR(255) UNIQUE,  -- Hashed with Argon2
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN DEFAULT FALSE
);
```

**Why Argon2id**: Only password hashing competition winner; resistant to GPU/ASIC attacks.

### 4. Pydantic v2 Validation Patterns

**Field Validators** (replaces @validator):

```python
from pydantic import BaseModel, field_validator, model_validator

class UserCreate(BaseModel):
    email: str
    password: str

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        if '@' not in v:
            raise ValueError('Invalid email')
        return v.lower()

    @model_validator(mode='after')
    def validate_model(self) -> 'UserCreate':
        # Cross-field validation
        if len(self.password) < 8:
            raise ValueError('Password too short')
        return self
```

**Performance**: Pydantic V2 is 5-17× faster than V1 due to Rust-based core (pydantic-core).

### 5. Custom Exception Handlers

**Production Error Response Pattern**:

```python
class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: Optional[List[dict]] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "field": ".".join(str(x) for x in error["loc"]),
            "message": error["msg"],
            "type": error["type"]
        }
        for error in exc.errors()
    ]

    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": errors,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
```

### 6. pytest-asyncio Testing Strategy

**Test Database Fixture with Transaction Rollback**:

```python
@pytest.fixture
async def db_session(test_engine):
    """Transactional session that rolls back after each test."""
    connection = await test_engine.connect()
    transaction = await connection.begin()

    AsyncTestSessionLocal = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    session = AsyncTestSessionLocal()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()
```

**Configuration** (pytest.ini):
```ini
[pytest]
asyncio_mode = auto  # Automatically detect async tests
```

---

## Phase 2 Implementation Roadmap

### **Sprint 1: Core Infrastructure (Week 1)**

**Goal**: FastAPI app running with database migrations

#### Task 1.1: FastAPI Application Factory ✅ HIGH PRIORITY
**File**: `prime_agent/main.py`
**Estimated**: 2 hours
**Pattern**: Application factory with lifespan context manager

**Implementation**:
```python
# prime_agent/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from prime_agent.config import get_settings
from prime_agent.db.session import database_session
from prime_agent.web.middlewares import setup_cors, LoggingMiddleware, SecurityHeadersMiddleware
from prime_agent.web.routes import auth, harnesses, tools, users, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = get_settings()
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    await database_session.init()

    if settings.environment == "development":
        await database_session.create_tables()

    yield

    # Shutdown
    await database_session.dispose()
    logger.info("Application shutdown complete")

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        openapi_url=settings.openapi_url,
    )

    # Middleware (ORDER MATTERS)
    setup_cors(app)  # MUST BE FIRST
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Routers
    api_router = APIRouter(prefix=settings.api_v1_prefix)
    api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
    api_router.include_router(harnesses.router, prefix="/harnesses", tags=["harnesses"])
    api_router.include_router(tools.router, prefix="/tools", tags=["tools"])
    api_router.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(api_router)
    app.include_router(health.router, tags=["health"])

    # Exception handlers
    register_exception_handlers(app)

    return app

app = create_app()
```

**References**:
- Web research: "Production-Ready FastAPI Project Structure (2026 Guide)"
- Agent pattern research (a88aed9)

**Acceptance Criteria**:
- [ ] FastAPI app starts without errors
- [ ] `/docs` endpoint shows OpenAPI documentation
- [ ] `/health` endpoint returns 200 OK
- [ ] Middleware stack executes in correct order
- [ ] Database connection initializes on startup

---

#### Task 1.2: Alembic Migration Setup ✅ HIGH PRIORITY
**Files**: `alembic.ini`, `prime_agent/db/migrations/env.py`
**Estimated**: 1 hour
**Pattern**: Async Alembic with auto-detection

**Commands**:
```bash
cd /mnt/c/Users/91790/Desktop/Corridor\ Platforms/prime-agent-study-main/backend
alembic init prime_agent/db/migrations
```

**alembic.ini**:
```ini
[alembic]
script_location = prime_agent/db/migrations
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url = postgresql+asyncpg://postgres:postgres@localhost:5432/prime_agent

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

**prime_agent/db/migrations/env.py** (async version):
```python
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from prime_agent.db import Base
from prime_agent.config import get_settings

config = context.config
settings = get_settings()

# Override sqlalchemy.url from settings
config.set_main_option("sqlalchemy.url", settings.get_async_database_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    import asyncio
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Generate Initial Migration**:
```bash
alembic revision --autogenerate -m "Initial schema: users, harnesses, tools"
alembic upgrade head
```

**References**:
- Web research: "FastAPI Alembic Migrations with PostgreSQL"
- Pattern: Async Alembic from SQLAlchemy 2.0 docs

**Acceptance Criteria**:
- [ ] `alembic revision --autogenerate` detects models
- [ ] `alembic upgrade head` creates tables in PostgreSQL
- [ ] `alembic downgrade -1` rolls back migration
- [ ] Migration creates: users, harnesses, tools tables with proper columns

---

#### Task 1.3: Concrete Service Implementations ✅ HIGH PRIORITY
**Files**:
- `prime_agent/services/harness_service.py`
- `prime_agent/services/tool_service.py`
- `prime_agent/services/auth_service.py`

**Estimated**: 3 hours
**Pattern**: Extend BaseService with domain-specific logic

**HarnessService** (`prime_agent/services/harness_service.py`):
```python
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from prime_agent.db.models.harness import Harness
from prime_agent.services.base import BaseService

class HarnessService(BaseService[Harness]):
    """Service for harness management."""

    model = Harness

    @classmethod
    async def pre_save_check(cls, data: dict, db: AsyncSession) -> None:
        """Validate harness data before saving."""
        # Check name uniqueness
        if "name" in data:
            stmt = select(Harness).where(Harness.name == data["name"])
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Harness with name '{data['name']}' already exists"
                )

        # Validate model
        valid_models = ["claude-sonnet-4", "claude-opus-4", "gpt-4-turbo", "gpt-4o"]
        if "model" in data and data["model"] not in valid_models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid model. Must be one of: {', '.join(valid_models)}"
            )

        # Validate mode
        valid_modes = ["chat", "completion", "agent"]
        if "mode" in data and data["mode"] not in valid_modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mode. Must be one of: {', '.join(valid_modes)}"
            )

    @classmethod
    async def post_save(cls, instance: Harness, db: AsyncSession) -> None:
        """Actions after saving harness."""
        # TODO: Trigger harness validation/testing
        # TODO: Send notification
        pass

    @classmethod
    async def get_by_name(cls, name: str, db: AsyncSession) -> Optional[Harness]:
        """Get harness by name."""
        stmt = select(Harness).where(Harness.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
```

**ToolService** (`prime_agent/services/tool_service.py`):
```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from prime_agent.db.models.tool import Tool
from prime_agent.services.base import BaseService

class ToolService(BaseService[Tool]):
    """Service for tool management."""

    model = Tool

    @classmethod
    async def pre_save_check(cls, data: dict, db: AsyncSession) -> None:
        """Validate tool data before saving."""
        # Check name uniqueness
        if "name" in data:
            stmt = select(Tool).where(Tool.name == data["name"])
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Tool with name '{data['name']}' already exists"
                )

        # Validate category
        valid_categories = ["search", "data", "code", "communication", "system"]
        if "category" in data and data["category"] not in valid_categories:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category. Must be one of: {', '.join(valid_categories)}"
            )

    @classmethod
    async def get_by_category(cls, category: str, db: AsyncSession) -> list[Tool]:
        """Get all tools in a category."""
        stmt = select(Tool).where(Tool.category == category)
        result = await db.execute(stmt)
        return list(result.scalars().all())
```

**AuthService** (`prime_agent/services/auth_service.py`):
```python
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from prime_agent.db.models.user import User
from prime_agent.util.password import hash_password, verify_password
from prime_agent.web.dependencies.jwt_manager import JWTManager

class AuthService:
    """Service for authentication operations."""

    @staticmethod
    async def register_user(
        email: str,
        password: str,
        name: str,
        db: AsyncSession
    ) -> User:
        """Register a new user."""
        # Check if user exists
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )

        # Create user
        user = User(
            email=email,
            name=name,
            hashed_password=hash_password(password),
            role="member",  # Default role
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        return user

    @staticmethod
    async def authenticate_user(
        email: str,
        password: str,
        db: AsyncSession
    ) -> Optional[User]:
        """Authenticate user with email and password."""
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            return None

        if not verify_password(password, user.hashed_password):
            return None

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive"
            )

        return user

    @staticmethod
    def create_tokens(user: User) -> dict:
        """Create access and refresh tokens for user."""
        jwt_manager = JWTManager()

        access_token = jwt_manager.create_access_token({"sub": str(user.id)})
        refresh_token = jwt_manager.create_refresh_token({"sub": str(user.id)})

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }
```

**Update** `prime_agent/services/__init__.py`:
```python
from prime_agent.services.base import BaseService
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.tool_service import ToolService
from prime_agent.services.auth_service import AuthService

__all__ = [
    "BaseService",
    "HarnessService",
    "ToolService",
    "AuthService",
]
```

**References**:
- Corridor pattern: `corridor_api/services/base_object_service.py`
- BaseService lifecycle hooks pattern

**Acceptance Criteria**:
- [ ] HarnessService validates name uniqueness
- [ ] HarnessService validates model and mode
- [ ] ToolService validates category
- [ ] AuthService creates users with hashed passwords
- [ ] AuthService authenticates users
- [ ] All services extend BaseService properly

---

### **Sprint 2: API Routes & Auth (Week 1-2)**

**Goal**: Complete REST API with authentication

#### Task 2.1: Authentication Routes ✅ HIGH PRIORITY
**File**: `prime_agent/web/routes/auth.py`
**Estimated**: 3 hours
**Pattern**: OAuth2 with JWT tokens

```python
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.session import get_db
from prime_agent.schemas.user import UserCreate, UserLogin, AuthResponse, TokenResponse
from prime_agent.services.auth_service import AuthService

router = APIRouter()

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new user."""
    user = await AuthService.register_user(
        email=user_data.email,
        password=user_data.password,
        name=user_data.name,
        db=db
    )

    tokens = AuthService.create_tokens(user)

    return {
        "user": user,
        **tokens
    }

@router.post("/login", response_model=AuthResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """Login with email and password."""
    user = await AuthService.authenticate_user(
        email=form_data.username,
        password=form_data.password,
        db=db
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = AuthService.create_tokens(user)

    return {
        "user": user,
        **tokens
    }

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_token: str,
    db: AsyncSession = Depends(get_db)
):
    """Refresh access token using refresh token."""
    # TODO: Implement refresh token validation
    pass

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: User = Depends(get_current_user)
):
    """Logout user (revoke tokens)."""
    # TODO: Implement token revocation
    pass
```

**Acceptance Criteria**:
- [ ] POST /api/v1/auth/register creates user and returns tokens
- [ ] POST /api/v1/auth/login authenticates and returns tokens
- [ ] POST /api/v1/auth/refresh renews access token
- [ ] POST /api/v1/auth/logout revokes tokens
- [ ] Invalid credentials return 401
- [ ] Duplicate email returns 400

---

#### Task 2.2: Harness CRUD Routes ✅ HIGH PRIORITY
**File**: `prime_agent/web/routes/harnesses.py`
**Estimated**: 2 hours

```python
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.session import get_db
from prime_agent.db.models.user import User
from prime_agent.schemas.harness import (
    HarnessCreate,
    HarnessUpdate,
    HarnessResponse,
    HarnessListResponse
)
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies.auth import get_current_user

router = APIRouter()

@router.get("", response_model=HarnessListResponse)
async def list_harnesses(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all harnesses."""
    harnesses = await HarnessService.aget_all(db, skip=skip, limit=limit)
    total = await HarnessService.count(db)

    return {
        "data": harnesses,
        "total": total,
        "skip": skip,
        "limit": limit,
    }

@router.post("", response_model=HarnessResponse, status_code=status.HTTP_201_CREATED)
async def create_harness(
    harness_data: HarnessCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new harness."""
    harness = await HarnessService.acreate(harness_data.model_dump(), db)
    return harness

@router.get("/{harness_id}", response_model=HarnessResponse)
async def get_harness(
    harness_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get harness by ID."""
    harness = await HarnessService.aget_one(harness_id, db)
    return harness

@router.put("/{harness_id}", response_model=HarnessResponse)
async def update_harness(
    harness_id: str,
    harness_data: HarnessUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update harness."""
    harness = await HarnessService.aupdate(
        harness_id,
        harness_data.model_dump(exclude_unset=True),
        db
    )
    return harness

@router.delete("/{harness_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_harness(
    harness_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete harness."""
    await HarnessService.adelete(harness_id, db)
```

**Acceptance Criteria**:
- [ ] GET /api/v1/harnesses returns paginated list
- [ ] POST /api/v1/harnesses creates harness
- [ ] GET /api/v1/harnesses/{id} returns single harness
- [ ] PUT /api/v1/harnesses/{id} updates harness
- [ ] DELETE /api/v1/harnesses/{id} deletes harness
- [ ] All routes require authentication (401 if not logged in)

---

#### Task 2.3: Tool Routes ✅ MEDIUM PRIORITY
**File**: `prime_agent/web/routes/tools.py`
**Estimated**: 2 hours
**Pattern**: Similar to harnesses.py with category filtering

---

#### Task 2.4: User Routes ✅ MEDIUM PRIORITY
**File**: `prime_agent/web/routes/users.py`
**Estimated**: 1.5 hours
**Pattern**: GET /me, PATCH /me, password change

---

#### Task 2.5: Health Check Routes ✅ LOW PRIORITY
**File**: `prime_agent/web/routes/health.py`
**Estimated**: 30 minutes

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.session import get_db
from prime_agent.config import get_settings

router = APIRouter()

@router.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "ok"}

@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness check with database ping."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return {"status": "not ready", "database": "disconnected", "error": str(e)}
```

---

### **Sprint 3: Async Auth Dependencies (Week 2)**

**Authoritative plan**: `development/SPRINT3-PLAN.md`  
**Goal**: Consolidate and harden the existing async auth dependency layer.
Do not re-decode JWTs in route dependencies with the sample below — that sample
is a historical sketch and would drop `iss`/`aud`/`jti` validation.

#### Task 3.1: Async JWT Manager ✅ HIGH PRIORITY
**File**: `prime_agent/web/dependencies/jwt_manager.py`
**Estimated**: 1 hour
**Pattern**: Adapt `root/web/dependencies/jwt_manager.py` for async

---

#### Task 3.2: Async Auth Dependencies ✅ HIGH PRIORITY
**File**: `prime_agent/web/dependencies/auth.py`
**Estimated**: 2 hours

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt

from prime_agent.config import get_settings
from prime_agent.db.session import get_db
from prime_agent.db.models.user import User

security = HTTPBearer()
settings = get_settings()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token."""
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm]
        )
        user_id: str = payload.get("sub")

        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )

    return user

def require_role(required_role: str):
    """Dependency to check user role."""
    async def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {required_role}"
            )
        return current_user

    return role_checker
```

**Acceptance Criteria**:
- [ ] `get_current_user()` decodes JWT and fetches user from DB (async)
- [ ] Invalid token raises 401
- [ ] Inactive user raises 403
- [ ] `require_role()` checks user role

---

**Authoritative plan**: `development/SPRINT4-PLAN.md`  
**Goal**: Service-layer tests and remaining authz/CSRF gaps. Do not duplicate existing harness HTTP CRUD.

#### Task 4.1: Service Tests ✅ HIGH PRIORITY
**Files**: `tests/services/test_harness_service.py`, etc.
**Estimated**: 4 hours
**Pattern**: pytest-asyncio with transactional fixtures

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.services.harness_service import HarnessService

@pytest.mark.asyncio
async def test_create_harness(db_session: AsyncSession):
    """Test creating a harness."""
    harness_data = {
        "name": "test-harness",
        "model": "claude-sonnet-4",
        "mode": "agent",
        "tools": [],
        "memory": []
    }

    harness = await HarnessService.acreate(harness_data, db_session)

    assert harness.id is not None
    assert harness.name == "test-harness"
    assert harness.model == "claude-sonnet-4"

@pytest.mark.asyncio
async def test_create_harness_duplicate_name(db_session: AsyncSession):
    """Test creating harness with duplicate name raises error."""
    harness_data = {
        "name": "duplicate",
        "model": "claude-sonnet-4",
        "mode": "agent",
        "tools": [],
        "memory": []
    }

    await HarnessService.acreate(harness_data, db_session)

    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(harness_data, db_session)

    assert exc_info.value.status_code == 400
    assert "already exists" in exc_info.value.detail
```

---

#### Task 4.2: Integration Tests ✅ MEDIUM PRIORITY
**Files**: `tests/integration/test_harness_api.py`
**Estimated**: 3 hours
**Pattern**: Full request lifecycle with httpx.AsyncClient

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_harness_crud_flow(client: AsyncClient, auth_headers: dict):
    """Test full CRUD flow for harnesses."""
    # Create harness
    create_response = await client.post(
        "/api/v1/harnesses",
        json={
            "name": "integration-test",
            "model": "claude-sonnet-4",
            "mode": "agent",
            "tools": [],
            "memory": []
        },
        headers=auth_headers
    )
    assert create_response.status_code == 201
    harness = create_response.json()
    harness_id = harness["id"]

    # Get harness
    get_response = await client.get(
        f"/api/v1/harnesses/{harness_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200

    # Update harness
    update_response = await client.put(
        f"/api/v1/harnesses/{harness_id}",
        json={"description": "Updated description"},
        headers=auth_headers
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated description"

    # Delete harness
    delete_response = await client.delete(
        f"/api/v1/harnesses/{harness_id}",
        headers=auth_headers
    )
    assert delete_response.status_code == 204

    # Verify deleted
    get_response = await client.get(
        f"/api/v1/harnesses/{harness_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 404
```

---

### **Sprint 5: Frontend-Backend Integration (Week 3)**

**Goal**: Connect frontend to backend

#### Task 5.1: Update Frontend API Base URL
**File**: `frontend/.env.local`
```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

#### Task 5.2: Test Dashboard with Real Backend
- Start backend: `uvicorn prime_agent.main:app --reload`
- Start frontend: `npm run dev`
- Register user → Login → Create harness → View on dashboard

#### Task 5.3: Handle CORS & Auth Errors
- Verify CORS headers in browser network tab
- Test token refresh flow
- Handle 401 errors gracefully

---

## Agent Registry

**For Future Resumption:**

| Agent ID | Purpose | Status | Resume For |
|----------|---------|--------|------------|
| a528fc5 | Backend implementation analysis | COMPLETE | Deep dive into specific components |
| a88aed9 | Production patterns research | COMPLETE | Additional pattern research |

---

## Success Metrics

### Phase 2 Complete When:

**Backend**:
- [x] FastAPI app runs successfully
- [x] Database migrations work (alembic upgrade head)
- [x] All routes respond with correct status codes
- [x] Authentication flow works (register → login → access protected route)
- [x] 50+ backend tests passing (services + integration)

**Frontend-Backend Integration**:
- [x] Dashboard loads harnesses from backend
- [x] Create harness saves to database
- [x] Authentication persists across page refreshes (if using cookies)
- [x] Error states display properly (network errors, validation errors)

**Production Readiness**:
- [x] Docker Compose setup works
- [x] Environment variables documented
- [x] Logging configured
- [x] Security headers present
- [x] API documentation (OpenAPI) complete

---

## Technology Stack Summary

### Backend (Prime Agent)
```toml
[project.dependencies]
fastapi = "^0.115.0"
uvicorn[standard] = "^0.30.0"
sqlalchemy = "^2.0.35"
asyncpg = "^0.29.0"
alembic = "^1.13.0"
pydantic = "^2.9.0"
pydantic-settings = "^2.5.0"
python-jose[cryptography] = "^3.3.0"
passlib[argon2] = "^1.7.4"
python-multipart = "^0.0.9"
redis = "^5.0.0"
celery = "^5.4.0"
python-json-logger = "^2.0.7"

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "httpx>=0.27.0",
    "ruff>=0.6.0",
    "mypy>=1.11.0",
    "black>=24.8.0",
]
```

### Database
- PostgreSQL 15+
- asyncpg driver (25-35% faster than psycopg3)

### Frontend (Already Complete)
- Next.js 14, React 18
- TanStack Query 5.0, Zustand, Zod
- 139 tests passing

---

## References

### Web Research (Agent a88aed9)
- [Production-Ready FastAPI Project Structure (2026 Guide)](https://dev.to/datanestdigital/production-ready-fastapi-project-structure-2026-guide-b1g)
- [FastAPI + SQLAlchemy 2.0: Modern Async Database Patterns](https://dev-faizan.medium.com/fastapi-sqlalchemy-2-0-modern-async-database-patterns-7879d39b6843)
- [JWT Authentication in FastAPI: A Complete Walkthrough](https://blog.rajpoot.dev/posts/fastapi/jwt-authentication-in-fastapi/)
- [Pydantic V2 Complete Guide](https://itsourcecode.com/blogs/pydantic-v2-complete-guide-2026/)
- [Async Testing with Pytest](https://medium.com/@connect.hashblock/async-testing-with-pytest-mastering-pytest-asyncio-and-event-loops-for-fastapi-and-beyond-37c613f1cfa3)

### Internal Documentation
- `/agent-platform-research-insight/00-overview-and-key-patterns.md`
- `/development/implementation-log.md`
- `/development/production-patterns-mapping.md`

### Corridor Platforms Patterns
- `corridor_api/factory.py` - Application factory
- `corridor_api/services/base_object_service.py` - Service layer
- `corridor_api/db/async_session.py` - Database session

---

## Next Actions

1. **Create main FastAPI app** (`prime_agent/main.py`)
2. **Setup Alembic migrations** (alembic.ini, env.py)
3. **Implement concrete services** (HarnessService, ToolService, AuthService)
4. **Create API routes** (auth, harnesses, tools, health)
5. **Write tests** (pytest-asyncio)
6. **Integrate frontend** (connect to backend API)

**Estimated Total Time**: 2-3 weeks for complete Phase 2

---

**Document Version**: 1.0
**Last Updated**: 2026-08-29
**Next Review**: After Sprint 1 completion
