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
    "ApprovalRow",
    "Base",
    "CatalogArtifactRow",
    "CommandRow",
    "EventRow",
    "HarnessVersionRow",
    "ScopeGrantRow",
    "ScopeMembershipRow",
    "ScopeRow",
    "SessionIndexRow",
    "SessionRow",
    "ToolOutboxRow",
    "WorkspaceFileRow",
    "check_health",
    "create_all",
    "drop_all",
    "get_database_url",
    "make_engine",
    "open_db",
    "session_factory",
]
