"""Database layer: async engine, models, and connection pooling.

This module provides production-ready database infrastructure:
- AsyncEngine with connection pooling
- SQLAlchemy ORM models
- Session factory for async operations
- Health check utilities

Example usage:
    from supervisor.db import open_db, SessionRow

    async with open_db() as (engine, db):
        async with db() as session:
            row = SessionRow(id="...", tenant_id="default", runtime="githi")
            session.add(row)
            await session.commit()
"""

from supervisor.db.engine import (
    check_health,
    create_all,
    drop_all,
    get_database_url,
    get_pool_status,
    make_engine,
    open_db,
    session_factory,
)
from supervisor.db.models import (
    ApprovalRow,
    Base,
    CatalogArtifactRow,
    CommandRow,
    EventRow,
    HarnessVersionRow,
    ScopeGrantRow,
    ScopeMembershipRow,
    ScopeRow,
    SessionIndexRow,
    SessionRow,
    ToolOutboxRow,
    WorkspaceFileRow,
)

__all__ = [
    # Engine
    "make_engine",
    "create_all",
    "drop_all",
    "session_factory",
    "open_db",
    "get_database_url",
    "check_health",
    "get_pool_status",
    # Base
    "Base",
    # Models
    "SessionRow",
    "EventRow",
    "SessionIndexRow",
    "CommandRow",
    "HarnessVersionRow",
    "CatalogArtifactRow",
    "ApprovalRow",
    "ToolOutboxRow",
    "ScopeRow",
    "WorkspaceFileRow",
    "ScopeMembershipRow",
    "ScopeGrantRow",
]
