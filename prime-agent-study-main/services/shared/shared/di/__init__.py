"""
Dependency Injection module.

Provides a lightweight, async-native DI container with:
- Singleton lifetime (one instance per container)
- Scoped lifetime (one instance per scope/request/session)
- Transient lifetime (new instance every resolution)

Usage:
    from shared.di import Container, Disposable

    container = Container()
    container.register_singleton(Database, lambda c: PostgresDatabase(url))
    container.register_scoped(SessionStore, lambda c: SessionStore(c.resolve(Database)))

    async with container.scope("request-123") as scoped:
        store = await scoped.resolve(SessionStore)
"""

from shared.di.container import (
    Container,
    ScopedContainer,
    Disposable,
    Registration,
    Lifetime,
    DependencyNotFoundError,
    CircularDependencyError,
)

__all__ = [
    "Container",
    "ScopedContainer",
    "Disposable",
    "Registration",
    "Lifetime",
    "DependencyNotFoundError",
    "CircularDependencyError",
]
