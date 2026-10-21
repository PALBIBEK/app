"""
Application settings using Pydantic v2.

Following corridor-platforms pattern with:
- Environment-based configuration
- Secret management
- Type-safe settings
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Following corridor-platforms pattern for configuration management.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "Prime Agent API"
    app_version: str = "0.1.0"
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/prime_agent",
        description="PostgreSQL database URL with asyncpg driver",
    )
    database_echo: bool = False  # Echo SQL statements

    # Security
    secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION_USE_STRONG_SECRET",
        description="Secret key for JWT signing (CHANGE IN PRODUCTION)",
    )
    algorithm: str = "HS256"
    jwt_issuer: str = "prime-agent-api"
    jwt_audience: str = "prime-agent-web"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "prime_agent_refresh"
    refresh_cookie_secure: bool = False
    csrf_cookie_name: str = "prime_agent_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    # CORS
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
        ],
        description="Allowed CORS origins",
    )
    cors_allow_credentials: bool = True

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Redis (for Celery)
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str | None = None  # Defaults to redis_url
    celery_result_backend: str | None = None  # Defaults to redis_url

    # AI plane (D1) — never expose these to NEXT_PUBLIC_*
    ai_supervisor_base_url: str | None = None
    ai_service_jwt_secret: str | None = None
    ai_service_jwt_issuer: str = "prime-agent-api"
    ai_service_jwt_audience: str = "yal-supervisor"
    ai_service_jwt_ttl_seconds: int = 60
    ai_service_id: str = "prime-agent-bff"
    ai_http_timeout_seconds: float = 5.0
    ai_sse_connect_timeout_seconds: float = 10.0
    ai_platform_status_cache_ttl_seconds: float = 10.0
    ai_session_create_timeout_seconds: float = 60.0
    """Starting a session resolves and checks its sandbox, which can take longer than a plain call."""

    # Where a new harness's commands run until its owner changes it in the builder. Docker
    # isolates them; ./dev.sh sets host process with full access so a laptop needs no Docker
    # image (commands still ask for approval first).
    harness_default_sandbox_provider: Literal["none", "local_process", "local_docker", "e2b"] = "local_docker"
    harness_default_file_effects: Literal["read_only", "workspace_write", "danger_full_access"] = "workspace_write"

    # D13 Trace API — Jaeger query proxy (MVP); never hardcode in route modules.
    jaeger_query_base_url: str = "http://127.0.0.1:16686"

    @field_validator("celery_broker_url", mode="before")
    @classmethod
    def set_celery_broker_url(cls, v: str | None, info) -> str:
        """Default celery_broker_url to redis_url if not set."""
        if v is None:
            return info.data.get("redis_url", "redis://localhost:6379/0")
        return v

    @field_validator("celery_result_backend", mode="before")
    @classmethod
    def set_celery_result_backend(cls, v: str | None, info) -> str:
        """Default celery_result_backend to redis_url if not set."""
        if v is None:
            return info.data.get("redis_url", "redis://localhost:6379/0")
        return v

    # API Configuration
    api_v1_prefix: str = "/api/v1"
    docs_url: str | None = "/docs"  # None to disable in production
    redoc_url: str | None = "/redoc"  # None to disable in production
    openapi_url: str | None = "/openapi.json"  # None to disable in production

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Reject insecure secrets and cookie settings in production."""
        if self.environment == "production":
            if len(self.secret_key.encode("utf-8")) < 32 or self.secret_key.startswith("CHANGE_ME"):
                raise ValueError("SECRET_KEY must be a random value of at least 32 bytes in production")
            if not self.refresh_cookie_secure:
                raise ValueError("REFRESH_COOKIE_SECURE must be enabled in production")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origins are forbidden in production")
            if not self.ai_supervisor_base_url:
                raise ValueError("AI_SUPERVISOR_BASE_URL is required in production")
            if not self.ai_service_jwt_secret or len(self.ai_service_jwt_secret.encode("utf-8")) < 32:
                raise ValueError("AI_SERVICE_JWT_SECRET must be at least 32 bytes in production")
            if self.ai_service_jwt_secret == self.secret_key:
                raise ValueError("AI_SERVICE_JWT_SECRET must differ from SECRET_KEY")
        return self

    def get_async_database_url(self) -> str:
        """
        Get the async database URL.

        Ensures we're using the asyncpg driver for PostgreSQL.
        """
        url = str(self.database_url)
        if "postgresql://" in url and "postgresql+asyncpg://" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return url


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Use this function to get settings in FastAPI dependencies:
        ```python
        def my_endpoint(settings: Settings = Depends(get_settings)):
            ...
        ```
    """
    return Settings()
