"""Supervisor configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from supervisor.catalog.loading.bootstrap import agent_platform_root, resolve_catalog_dir


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


def _optional_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return float(raw)


@dataclass(frozen=True, slots=True)
class TemporalSettings:
    """Where sessions run durably. Every session is a workflow on this task queue."""

    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "supervisor-sessions"
    # Temporal Cloud API key; setting it also turns on TLS.
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> TemporalSettings:
        return cls(
            address=os.environ.get("TEMPORAL_ADDRESS", cls.address),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", cls.namespace),
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", cls.task_queue),
            api_key=os.environ.get("TEMPORAL_API_KEY") or None,
        )


@dataclass
class Settings:
    """Runtime settings for the supervisor service."""

    host: str = "0.0.0.0"
    port: int = 8080
    sandbox_provider: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_PROVIDER", "none")
    )
    drain_grace_s: float = 30.0
    idle_reap_max_seconds: float = 0.0
    idle_reap_interval_seconds: float = 60.0
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "sqlite+aiosqlite:///:memory:"
        )
    )
    blob_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("BLOB_DIR", str(agent_platform_root() / "data" / "blobs"))
        )
    )
    catalog_dir: Path | None = None
    harness_versions_dir: Path = field(
        default_factory=lambda: agent_platform_root() / "harness" / "versions"
    )
    load_composition_catalog: bool = True
    temporal: TemporalSettings = field(default_factory=TemporalSettings.from_env)
    persist_composition_catalog: bool = field(
        default_factory=lambda: os.environ.get("YAL_PERSIST_CATALOG", "true").lower()
        in ("1", "true", "yes")
    )
    auth_token: str | None = field(
        default_factory=lambda: os.environ.get("AUTH_TOKEN") or None
    )
    auth_required: bool = field(
        default_factory=lambda: os.environ.get("AUTH_REQUIRED", "").lower()
        in ("1", "true", "yes")
        or os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).lower()
        in ("production", "staging")
    )
    service_jwt_secret: str | None = field(
        default_factory=lambda: os.environ.get("SERVICE_JWT_SECRET")
        or os.environ.get("AI_SERVICE_JWT_SECRET")
        or None
    )
    service_jwt_audience: str = field(
        default_factory=lambda: os.environ.get("SERVICE_JWT_AUDIENCE", "yal-supervisor")
    )
    service_jwt_issuer: str = field(
        default_factory=lambda: os.environ.get("SERVICE_JWT_ISSUER", "prime-agent-api")
    )
    multi_tenant_strict: bool = field(
        default_factory=lambda: os.environ.get("MULTI_TENANT_STRICT", "false").lower()
        in ("1", "true", "yes")
    )
    tenant_max_sessions: int | None = field(
        default_factory=lambda: _optional_int("TENANT_MAX_SESSIONS")
    )
    tenant_max_sandboxes: int | None = field(
        default_factory=lambda: _optional_int("TENANT_MAX_SANDBOXES")
    )
    global_max_sessions: int | None = field(
        default_factory=lambda: _optional_int("GLOBAL_MAX_SESSIONS")
    )
    tenant_max_cost: float | None = field(
        default_factory=lambda: _optional_float("TENANT_MAX_COST_PER_TENANT")
    )
    tenant_rate_limit_per_minute: int | None = field(
        default_factory=lambda: _optional_int("TENANT_RATE_LIMIT_PER_MINUTE")
    )

    @classmethod
    def from_env(cls) -> Settings:
        catalog_dir_raw = os.environ.get("YAL_CATALOG_DIR")
        load_catalog = os.environ.get("YAL_LOAD_COMPOSITION_CATALOG", "true").lower() not in (
            "0",
            "false",
            "no",
        )
        return cls(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            sandbox_provider=os.environ.get("SANDBOX_PROVIDER", "none"),
            drain_grace_s=float(os.environ.get("DRAIN_GRACE_S", "30")),
            idle_reap_max_seconds=float(os.environ.get("IDLE_REAP_MAX_SECONDS", "0")),
            idle_reap_interval_seconds=float(
                os.environ.get("IDLE_REAP_INTERVAL_SECONDS", "60")
            ),
            database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
            # Prefer env paths so Docker images (shallow tree) do not call agent_platform_root().
            blob_dir=Path(
                os.environ["BLOB_DIR"]
                if os.environ.get("BLOB_DIR")
                else str(agent_platform_root() / "data" / "blobs")
            ),
            catalog_dir=resolve_catalog_dir(catalog_dir_raw) if catalog_dir_raw else None,
            harness_versions_dir=Path(
                os.environ["HARNESS_VERSIONS_DIR"]
                if os.environ.get("HARNESS_VERSIONS_DIR")
                else str(agent_platform_root() / "harness" / "versions")
            ),
            load_composition_catalog=load_catalog,
            temporal=TemporalSettings.from_env(),
            persist_composition_catalog=os.environ.get(
                "YAL_PERSIST_CATALOG", "true"
            ).lower()
            in ("1", "true", "yes"),
            auth_token=os.environ.get("AUTH_TOKEN") or None,
            auth_required=os.environ.get("AUTH_REQUIRED", "").lower()
            in ("1", "true", "yes")
            or os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).lower()
            in ("production", "staging"),
            service_jwt_secret=os.environ.get("SERVICE_JWT_SECRET")
            or os.environ.get("AI_SERVICE_JWT_SECRET")
            or None,
            service_jwt_audience=os.environ.get("SERVICE_JWT_AUDIENCE", "yal-supervisor"),
            service_jwt_issuer=os.environ.get("SERVICE_JWT_ISSUER", "prime-agent-api"),
            multi_tenant_strict=os.environ.get("MULTI_TENANT_STRICT", "false").lower()
            in ("1", "true", "yes"),
            tenant_max_sessions=_optional_int("TENANT_MAX_SESSIONS"),
            tenant_max_sandboxes=_optional_int("TENANT_MAX_SANDBOXES"),
            global_max_sessions=_optional_int("GLOBAL_MAX_SESSIONS"),
            tenant_max_cost=_optional_float("TENANT_MAX_COST_PER_TENANT"),
            tenant_rate_limit_per_minute=_optional_int("TENANT_RATE_LIMIT_PER_MINUTE"),
        )

    def require_auth_configured(self) -> None:
        """Fail closed when AUTH_REQUIRED but no verifier is configured."""
        if self.auth_required and not self.service_jwt_secret and not self.auth_token:
            raise RuntimeError(
                "AUTH_REQUIRED is set but SERVICE_JWT_SECRET/AUTH_TOKEN is missing"
        )

    def resolved_catalog_dir(self) -> Path:
        return resolve_catalog_dir(self.catalog_dir)
