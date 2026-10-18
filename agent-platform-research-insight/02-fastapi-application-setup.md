# FastAPI Application Setup and Configuration

## Overview

This document covers the recommended patterns for setting up a FastAPI application, including application factory, middleware configuration, lifespan management, and ASGI server setup.

## Application Factory Pattern

### Basic Factory

```python
# app/factory.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

_app = None  # Singleton instance

def get_fastapi_app() -> FastAPI:
    """
    Factory function to create or return existing FastAPI application.
    Uses singleton pattern to prevent duplicate middleware registration.
    """
    global _app

    if _app is not None:
        return _app

    app = FastAPI(
        title="API Platform",
        version="1.0.0",
        description="Enterprise API Platform",
        default_response_class=OrderedJSONResponse,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # Configure middleware
    configure_middleware(app)

    # Register routes
    app.include_router(get_api_router())

    # Register exception handlers
    register_exception_handlers(app)

    _app = app
    return _app
```

### Why Singleton Pattern?

**Problem**: Creating multiple FastAPI instances can lead to:
- Duplicate middleware registration
- Memory leaks
- Inconsistent application state

**Solution**: Cache the application instance and return it on subsequent calls.

## Lifespan Management

### Async Context Manager Pattern

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Async context manager for application startup and shutdown.
    Runs once at application start and once at shutdown.
    """
    # Startup
    logger.info("Application starting up...")

    # Initialize database
    await init_database()

    # Validate license
    await validate_license()

    # Setup telemetry
    setup_telemetry()

    # Initialize cache
    await init_cache()

    # Start background services
    await start_background_services()

    logger.info("Application startup complete")

    yield  # Application running

    # Shutdown
    logger.info("Application shutting down...")

    # Close database connections
    await close_database()

    # Stop background services
    await stop_background_services()

    # Flush logs
    await flush_logs()

    logger.info("Application shutdown complete")
```

### Startup Tasks

Common startup operations:

1. **Database Initialization**
```python
async def init_database():
    """Initialize database connection pool"""
    from app.db.async_session import SQLAlchemyAsync

    # Create async engine
    SQLAlchemyAsync.initialize(settings.DATABASE_URI)

    # Run migrations if needed
    if settings.AUTO_MIGRATE:
        await run_migrations()
```

2. **License Validation**
```python
async def validate_license():
    """Validate application license"""
    from app.services.license_service import LicenseService

    if not LicenseService.validate():
        raise RuntimeError("Invalid or expired license")
```

3. **Telemetry Setup**
```python
def setup_telemetry():
    """Initialize error tracking and analytics"""
    if settings.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENV,
            traces_sample_rate=0.1,
        )
```

### Shutdown Tasks

Common shutdown operations:

1. **Close Database Connections**
```python
async def close_database():
    """Close database connection pool"""
    from app.db.async_session import SQLAlchemyAsync
    await SQLAlchemyAsync.close()
```

2. **Flush Logs**
```python
async def flush_logs():
    """Ensure all logs are written"""
    import logging
    for handler in logging.root.handlers:
        handler.flush()
```

## Middleware Configuration

### Middleware Stack Order

**Critical**: Middleware order matters! They execute in the order they're added.

```python
def configure_middleware(app: FastAPI):
    """
    Configure middleware stack.
    Order is important - first added is outermost layer.
    """

    # 1. CSP for API docs (outermost)
    if settings.DEBUG:
        app.add_middleware(CSPMiddleware)

    # 2. Request-level caching
    app.add_middleware(MemoizedCacheMiddleware)

    # 3. Database session scoping (CRITICAL: before auth)
    app.add_middleware(DBSessionMiddleware)

    # 4. Session middleware for OAuth
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="session_id",
        max_age=3600,
        https_only=not settings.DEBUG,
    )

    # 5. Security headers
    app.add_middleware(SecHeadersMiddleware)

    # 6. Base URL handling
    app.add_middleware(
        BaseUrlMiddleware,
        base_url=settings.BASE_URL,
    )

    # 7. Proxy headers (if behind proxy)
    if settings.BEHIND_PROXY:
        app.add_middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=settings.TRUSTED_HOSTS,
        )

    # 8. HTTP access logging
    app.add_middleware(LoggingMiddleware)

    # 9. GZIP compression (innermost, closest to response)
    if settings.ENABLE_GZIP:
        app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 10. CORS (if needed)
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
```

### Custom Middleware Examples

#### 1. Database Session Middleware

```python
from starlette.middleware.base import BaseHTTPMiddleware

class DBSessionMiddleware(BaseHTTPMiddleware):
    """
    Manages database session lifecycle per request.
    Creates session at request start, closes at request end.
    """

    async def dispatch(self, request: Request, call_next):
        from app.db.async_session import SQLAlchemyAsync

        # Create scoped session context
        with SQLAlchemyAsync.scoped_context():
            try:
                response = await call_next(request)
                return response
            finally:
                # Cleanup session
                await SQLAlchemyAsync.async_session.remove()
```

#### 2. Request Caching Middleware

```python
class MemoizedCacheMiddleware(BaseHTTPMiddleware):
    """
    Request-level caching for GET requests.
    Cache is scoped to single request to avoid stale data.
    """

    async def dispatch(self, request: Request, call_next):
        # Only cache GET requests
        if request.method != "GET":
            return await call_next(request)

        from app.libs.cache import RequestCache

        with RequestCache.scoped_context():
            return await call_next(request)
```

#### 3. Logging Middleware

```python
import time
from starlette.middleware.base import BaseHTTPMiddleware

class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all HTTP requests with timing information.
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Log request
        logger.info(f"Request: {request.method} {request.url.path}")

        try:
            response = await call_next(request)

            # Calculate duration
            duration = time.time() - start_time

            # Log response
            logger.info(
                f"Response: {response.status_code} "
                f"Duration: {duration:.3f}s"
            )

            return response
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Request failed: {request.method} {request.url.path} "
                f"Duration: {duration:.3f}s Error: {str(e)}"
            )
            raise
```

#### 4. Security Headers Middleware

```python
class SecHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to all responses.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # HSTS
        response.headers["Strict-Transport-Security"] = \
            "max-age=31536000; includeSubDomains"

        # XSS Protection
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # CSP
        response.headers["Content-Security-Policy"] = \
            "default-src 'self'; script-src 'self' 'unsafe-inline'"

        return response
```

## Custom Response Classes

### Ordered JSON Response

```python
import json
from typing import Any
from fastapi.responses import JSONResponse

class OrderedJSONResponse(JSONResponse):
    """
    JSON response with ordered keys for deterministic output.
    Useful for testing and debugging.
    """

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            sort_keys=True,  # Deterministic output
        ).encode("utf-8")
```

### Custom Response with Metadata

```python
from typing import Any, Optional

class APIResponse(OrderedJSONResponse):
    """
    Standardized API response format.
    """

    def __init__(
        self,
        data: Any = None,
        error: Optional[dict] = None,
        meta: Optional[dict] = None,
        status_code: int = 200,
    ):
        content = {
            "data": data,
            "error": error,
            "meta": meta or {},
        }
        super().__init__(content=content, status_code=status_code)
```

## Exception Handlers

### Global Exception Handler

```python
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.responses import JSONResponse

def register_exception_handlers(app: FastAPI):
    """Register global exception handlers"""

    # Validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError
    ):
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(x) for x in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Validation failed",
                    "details": errors,
                },
                "data": None,
            },
        )

    # HTTP exceptions
    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException
    ):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
                    "message": exc.detail,
                },
                "data": None,
            },
        )

    # Generic exceptions
    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request,
        exc: Exception
    ):
        logger.exception("Unhandled exception")

        # Don't expose internal errors in production
        message = str(exc) if settings.DEBUG else "Internal server error"

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": message,
                },
                "data": None,
            },
        )
```

## Configuration Management

### Dynaconf Setup

```python
# config/settings.py
from dynaconf import LazySettings

settings = LazySettings(
    # Support multiple environments
    environments=True,

    # Custom loaders
    loaders=[
        "app.config.encrypted_loader",  # Encrypted settings
        "dynaconf.loaders.env_loader",  # Environment variables
    ],

    # Environment variable prefix
    envvar_prefix="APP",

    # Environment switcher variable
    env_switcher="APP_ENV",  # APP_ENV=production

    # Settings files
    settings_file=[
        "config/settings.py",           # Base settings
        "{CONFIG_DIR}/api_config.py",   # User overrides
    ],

    # Validation
    validate=True,
)
```

### Environment-Specific Settings

```python
# config/settings.py (base)
DEBUG = False
DATABASE_URI = "sqlite:///app.db"
SECRET_KEY = "change-me-in-production"

# config/development_settings.py
DEBUG = True
DATABASE_URI = "sqlite:///dev.db"
CORS_ORIGINS = ["http://localhost:3000"]

# config/production_settings.py
DEBUG = False
DATABASE_URI = "postgresql://user:pass@host/db"
CORS_ORIGINS = ["https://app.example.com"]
```

### Encrypted Configuration

```python
# config/encrypted_loader.py
import base64
from cryptography.fernet import Fernet

class EncryptedLoader:
    """Load encrypted configuration values"""

    @staticmethod
    def load(settings, **kwargs):
        # Load encrypted file
        encrypted_file = kwargs.get("encrypted_file")
        if not encrypted_file:
            return

        # Decrypt using key from environment
        key = os.getenv("CONFIG_ENCRYPTION_KEY")
        if not key:
            raise ValueError("CONFIG_ENCRYPTION_KEY not set")

        fernet = Fernet(key.encode())

        with open(encrypted_file, "rb") as f:
            encrypted_data = f.read()

        decrypted_data = fernet.decrypt(encrypted_data)

        # Parse and merge into settings
        import json
        data = json.loads(decrypted_data)
        settings.update(data)
```

## ASGI Server Configuration

### Uvicorn Setup

```python
# main.py
import uvicorn
from app.factory import get_fastapi_app

if __name__ == "__main__":
    app = get_fastapi_app()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        workers=4,
        loop="uvloop",  # High-performance event loop
        log_level="info",
        access_log=True,
        use_colors=True,
    )
```

### Production Server Configuration

```python
# For production, use Gunicorn with Uvicorn workers
# gunicorn_conf.py
import multiprocessing

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "/var/log/gunicorn/access.log"
errorlog = "/var/log/gunicorn/error.log"
loglevel = "info"

# Process naming
proc_name = "api-platform"

# Server mechanics
daemon = False
pidfile = "/var/run/gunicorn.pid"
user = "www-data"
group = "www-data"
tmp_upload_dir = None

# SSL (if terminated at application level)
# keyfile = "/path/to/keyfile"
# certfile = "/path/to/certfile"
```

### Running with Gunicorn

```bash
# Development
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production
gunicorn app.main:app -c gunicorn_conf.py
```

## Health Checks

### Health Check Endpoint

```python
# app/web/health.py
from fastapi import APIRouter, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    cache: str

@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> HealthResponse:
    """Health check endpoint for load balancers"""

    # Check database
    try:
        from app.db.async_session import SQLAlchemyAsync
        async with SQLAlchemyAsync.async_session() as session:
            await session.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"

    # Check cache
    try:
        from app.libs.cache import cache
        cache.get("health_check")
        cache_status = "healthy"
    except Exception:
        cache_status = "unhealthy"

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        version=settings.VERSION,
        database=db_status,
        cache=cache_status,
    )

@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    """Readiness check for Kubernetes"""
    # Check if application is ready to serve traffic
    return {"ready": True}

@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check():
    """Liveness check for Kubernetes"""
    # Check if application is alive
    return {"alive": True}
```

## Best Practices

### 1. Application Initialization
- Use singleton pattern for FastAPI app
- Separate factory from application logic
- Initialize expensive resources in lifespan

### 2. Middleware
- Order middleware carefully
- Document middleware order with comments
- Use middleware for cross-cutting concerns only

### 3. Configuration
- Use environment-based configuration
- Never commit secrets to version control
- Validate configuration at startup

### 4. Error Handling
- Register global exception handlers
- Provide structured error responses
- Log exceptions appropriately

### 5. Production Deployment
- Use Gunicorn with Uvicorn workers
- Configure proper logging
- Set up health checks
- Use environment variables for secrets

## Conclusion

Proper FastAPI application setup is crucial for:
- Maintainability
- Security
- Performance
- Operational excellence

The patterns shown here provide a solid foundation for production-ready FastAPI applications.
