"""
FastAPI application factory with lifespan management.

Following 2026 production patterns:
- Lifespan context manager (replaces deprecated @app.on_event)
- Middleware registration in correct order (CORS first)
- Centralized exception handlers
- OpenAPI documentation configuration

References:
- Production-Ready FastAPI Project Structure (2026 Guide)
- PHASE2-BACKEND-ROADMAP.md
"""
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from prime_agent.config import get_settings
from prime_agent.db.session import db as database_session
from prime_agent.web.middlewares import (
    LoggingMiddleware,
    SecurityHeadersMiddleware,
    setup_cors,
)
from prime_agent.web.routes import (
    admin,
    ai,
    auth,
    catalog,
    harnesses,
    health,
    memory,
    platform,
    sessions,
    tools,
    traces,
    users,
    workspace,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Error response models
class ErrorDetail(BaseModel):
    """Validation error detail."""

    field: str
    message: str
    type: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    error_code: str
    message: str
    details: list[ErrorDetail] | None = None
    timestamp: str = Field(default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for startup and shutdown events.

    Following 2026 pattern: Resources opened before yield are available
    for the full application lifetime. Resources after yield are cleaned
    up on shutdown.

    Args:
        app: FastAPI application instance

    Yields:
        None: Application runs here
    """
    settings = get_settings()

    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")

    # Initialize database connection
    database_session.init(
        database_url=settings.get_async_database_url(),
        echo=settings.database_echo
    )
    logger.info("Database connection initialized")

    # Create tables in development (use Alembic in production)
    if settings.environment == "development":
        logger.info("Development mode: Creating database tables")
        await database_session.create_tables()

    logger.info("Application startup complete")

    yield  # Application runs here

    # Shutdown
    logger.info("Application shutdown initiated")

    # Dispose database connections
    await database_session.dispose()
    logger.info("Database connections disposed")

    logger.info("Application shutdown complete")


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register custom exception handlers.

    Provides consistent error responses across the API following
    production-grade patterns.

    Args:
        app: FastAPI application instance
    """

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """
        Handle Pydantic validation errors.

        Transforms validation errors into a standard error response format.

        Args:
            request: HTTP request
            exc: Validation exception

        Returns:
            JSONResponse with validation errors
        """
        errors = [
            ErrorDetail(
                field=".".join(str(x) for x in error["loc"]),
                message=error["msg"],
                type=error["type"],
            )
            for error in exc.errors()
        ]

        error_response = ErrorResponse(
            error_code="VALIDATION_ERROR",
            message="Request validation failed",
            details=errors,
        )

        return JSONResponse(
            status_code=422,
            content=error_response.model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Handle unexpected exceptions.

        Logs the full exception for debugging but returns a generic
        error message to the client (security best practice).

        Args:
            request: HTTP request
            exc: Exception

        Returns:
            JSONResponse with generic error message
        """
        logger.exception("Unhandled exception occurred", exc_info=exc)

        error_response = ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An internal error occurred. Please try again later.",
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response.model_dump(),
        )


def create_app() -> FastAPI:
    """
    Application factory for creating FastAPI instances.

    Following 2026 production pattern: Use factory instead of global
    app instance for better testability and configuration flexibility.

    Returns:
        FastAPI: Configured application instance
    """
    settings = get_settings()

    # Create FastAPI app with lifespan
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Production-ready FastAPI backend for AI agent platform",
        lifespan=lifespan,
        docs_url=settings.docs_url if settings.environment != "production" else None,
        redoc_url=settings.redoc_url if settings.environment != "production" else None,
        openapi_url=settings.openapi_url if settings.environment != "production" else None,
    )

    # Register middleware (ORDER MATTERS!)
    # CORS MUST BE FIRST to handle preflight requests
    setup_cors(app, settings)
    logger.info("CORS middleware registered")

    app.add_middleware(LoggingMiddleware)
    logger.info("Logging middleware registered")

    app.add_middleware(SecurityHeadersMiddleware)
    logger.info("Security headers middleware registered")

    # Register exception handlers
    register_exception_handlers(app)
    logger.info("Exception handlers registered")

    # Register routers
    # Health check routes (no authentication required)
    app.include_router(health.router, tags=["health"])
    logger.info("Health check routes registered")

    api_router = APIRouter(prefix=settings.api_v1_prefix)
    api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
    api_router.include_router(harnesses.router, prefix="/harnesses", tags=["harnesses"])
    api_router.include_router(catalog.router, prefix="/catalog", tags=["catalog"])
    api_router.include_router(tools.router, prefix="/tools", tags=["tools"])
    api_router.include_router(users.router, prefix="/users", tags=["users"])
    api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
    api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
    api_router.include_router(platform.router, prefix="/platform", tags=["platform"])
    api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
    api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
    api_router.include_router(traces.router, prefix="/traces", tags=["traces"])
    app.include_router(api_router)

    logger.info("Application factory complete")

    return app


# Create app instance
app = create_app()


# Development server entry point
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "prime_agent.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
