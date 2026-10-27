"""
API routes for Prime Agent.

This module exports all route modules.
"""
from prime_agent.web.routes import (
    admin,
    ai,
    auth,
    evaluations,
    harnesses,
    health,
    memory,
    operate,
    platform,
    sessions,
    tools,
    traces,
    users,
    workspace,
)

__all__ = [
    "admin",
    "ai",
    "auth",
    "evaluations",
    "harnesses",
    "health",
    "memory",
    "operate",
    "platform",
    "sessions",
    "tools",
    "traces",
    "users",
    "workspace",
]
