"""
Lightweight dependency injection container with scoped lifetimes.

Design Principles:
- Async-native: All resolution is async to support async factories
- Type-safe: Generic typing for compile-time safety
- Minimal: No reflection, no auto-wiring, explicit registration
- Testable: Easy to swap implementations for testing

Lifetimes:
- Singleton: One instance per container lifetime (app-wide)
- Scoped: One instance per scope (request, session, turn)
- Transient: New instance every resolution

Patterns Used:
- Protocol for Disposable interface
- Generic TypeVar for type safety
- Context manager for scoped lifetime
- Factory pattern for lazy instantiation

Example:
    ```python
    # Setup
    container = Container()

    # Register singleton (app-wide database pool)
    container.register_singleton(
        AsyncEngine,
        lambda c: create_async_engine(DATABASE_URL)
    )

    # Register scoped (per-request session)
    container.register_scoped(
        AsyncSession,
        lambda c: async_sessionmaker(c.resolve_sync(AsyncEngine))()
    )

    # Register transient (new instance every time)
    container.register_transient(
        UUIDGenerator,
        lambda c: UUIDGenerator()
    )

    # Usage in request handler
    async with container.scope("request-123") as scoped:
        session = await scoped.resolve(AsyncSession)
        # Use session...
    # Session automatically disposed
    ```
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    Protocol,
    TypeVar,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class Lifetime(Enum):
    """Dependency lifetime options."""

    SINGLETON = "singleton"  # One instance per container
    SCOPED = "scoped"  # One instance per scope
    TRANSIENT = "transient"  # New instance every resolution


@runtime_checkable
class Disposable(Protocol):
    """
    Protocol for resources that need cleanup.

    Implement this protocol for any dependency that holds resources
    (database connections, file handles, etc.).

    Example:
        ```python
        class DatabaseSession:
            async def dispose(self) -> None:
                await self._connection.close()
        ```
    """

    async def dispose(self) -> None:
        """Release resources held by this instance."""
        ...


@runtime_checkable
class SyncDisposable(Protocol):
    """Protocol for synchronous disposal."""

    def dispose(self) -> None:
        """Release resources synchronously."""
        ...


class DependencyNotFoundError(KeyError):
    """Raised when a dependency is not registered."""

    def __init__(self, interface: type) -> None:
        self.interface = interface
        super().__init__(f"No registration found for {interface.__name__}")


class CircularDependencyError(RuntimeError):
    """Raised when circular dependency is detected."""

    def __init__(self, chain: list[type]) -> None:
        self.chain = chain
        names = " -> ".join(t.__name__ for t in chain)
        super().__init__(f"Circular dependency detected: {names}")


class ScopeNotActiveError(RuntimeError):
    """Raised when trying to resolve scoped dependency outside a scope."""

    def __init__(self, interface: type) -> None:
        self.interface = interface
        super().__init__(
            f"Cannot resolve scoped dependency {interface.__name__} outside of a scope. "
            f"Use 'async with container.scope(scope_id)' to create a scope."
        )


# Factory types
SyncFactory = Callable[["Container"], T]
AsyncFactory = Callable[["Container"], Awaitable[T]]
Factory = SyncFactory[T] | AsyncFactory[T]


@dataclass(frozen=True)
class Registration(Generic[T]):
    """
    Registration entry for a dependency.

    Immutable to prevent accidental modification after registration.
    """

    interface: type[T]
    factory: Factory[T]
    lifetime: Lifetime
    is_async: bool = False

    @classmethod
    def singleton(
        cls,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> Registration[T]:
        """Create a singleton registration."""
        return cls(interface, factory, Lifetime.SINGLETON, is_async)

    @classmethod
    def scoped(
        cls,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> Registration[T]:
        """Create a scoped registration."""
        return cls(interface, factory, Lifetime.SCOPED, is_async)

    @classmethod
    def transient(
        cls,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> Registration[T]:
        """Create a transient registration."""
        return cls(interface, factory, Lifetime.TRANSIENT, is_async)


@dataclass
class Container:
    """
    Lightweight dependency injection container.

    Thread-safe for singleton resolution via asyncio.Lock.
    Scope-safe for scoped resolution via ScopedContainer.

    Design:
    - Registrations are stored by interface type
    - Singletons are cached after first resolution
    - Scoped dependencies require an active scope context
    - Transient dependencies are always freshly created

    Example:
        ```python
        container = Container()

        # Register dependencies
        container.register_singleton(Database, lambda c: Database(url))
        container.register_scoped(UnitOfWork, lambda c: UnitOfWork(c.resolve_sync(Database)))

        # Resolve singleton (no scope needed)
        db = await container.resolve(Database)

        # Resolve scoped (requires scope)
        async with container.scope("request-1") as scoped:
            uow = await scoped.resolve(UnitOfWork)
        ```
    """

    _registrations: dict[type, Registration[Any]] = field(default_factory=dict)
    _singletons: dict[type, Any] = field(default_factory=dict)
    _singleton_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _resolving: set[type] = field(default_factory=set)  # For circular dependency detection

    def register(self, registration: Registration[T]) -> None:
        """
        Register a dependency.

        Args:
            registration: Registration entry with interface, factory, and lifetime.

        Raises:
            ValueError: If interface is already registered.
        """
        if registration.interface in self._registrations:
            logger.warning(
                f"Overwriting registration for {registration.interface.__name__}"
            )
        self._registrations[registration.interface] = registration

    def register_singleton(
        self,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> None:
        """
        Register a singleton dependency.

        Singleton instances are created once and cached for the container's lifetime.

        Args:
            interface: The type/protocol to register.
            factory: Factory function that creates the instance.
            is_async: Whether the factory is async.

        Example:
            ```python
            container.register_singleton(
                Database,
                lambda c: create_database_pool(url)
            )
            ```
        """
        self.register(Registration.singleton(interface, factory, is_async))

    def register_scoped(
        self,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> None:
        """
        Register a scoped dependency.

        Scoped instances are created once per scope (e.g., per request, per session).

        Args:
            interface: The type/protocol to register.
            factory: Factory function that creates the instance.
            is_async: Whether the factory is async.

        Example:
            ```python
            container.register_scoped(
                DbSession,
                lambda c: SessionLocal()
            )
            ```
        """
        self.register(Registration.scoped(interface, factory, is_async))

    def register_transient(
        self,
        interface: type[T],
        factory: Factory[T],
        is_async: bool = False,
    ) -> None:
        """
        Register a transient dependency.

        Transient instances are created fresh on every resolution.

        Args:
            interface: The type/protocol to register.
            factory: Factory function that creates the instance.
            is_async: Whether the factory is async.

        Example:
            ```python
            container.register_transient(
                RequestId,
                lambda c: RequestId(uuid4())
            )
            ```
        """
        self.register(Registration.transient(interface, factory, is_async))

    def register_instance(self, interface: type[T], instance: T) -> None:
        """
        Register an existing instance as a singleton.

        Useful for configuration objects or externally created instances.

        Args:
            interface: The type/protocol to register.
            instance: The pre-created instance.

        Example:
            ```python
            config = load_config()
            container.register_instance(Config, config)
            ```
        """
        self._registrations[interface] = Registration.singleton(
            interface, lambda c: instance, is_async=False
        )
        self._singletons[interface] = instance

    async def resolve(self, interface: type[T]) -> T:
        """
        Resolve a dependency asynchronously.

        For singleton and transient lifetimes only.
        Scoped dependencies require a ScopedContainer.

        Args:
            interface: The type/protocol to resolve.

        Returns:
            The resolved instance.

        Raises:
            DependencyNotFoundError: If interface is not registered.
            ScopeNotActiveError: If interface is scoped and no scope is active.
            CircularDependencyError: If circular dependency is detected.

        Example:
            ```python
            db = await container.resolve(Database)
            ```
        """
        reg = self._registrations.get(interface)
        if reg is None:
            raise DependencyNotFoundError(interface)

        if reg.lifetime == Lifetime.SCOPED:
            raise ScopeNotActiveError(interface)

        if reg.lifetime == Lifetime.SINGLETON:
            return await self._resolve_singleton(reg)

        # Transient
        return await self._create_instance(reg)

    def resolve_sync(self, interface: type[T]) -> T:
        """
        Resolve a singleton synchronously (must already be created).

        Only works for singletons that have already been resolved
        or registered with register_instance().

        Args:
            interface: The type/protocol to resolve.

        Returns:
            The resolved instance.

        Raises:
            DependencyNotFoundError: If interface is not registered.
            RuntimeError: If singleton hasn't been created yet.

        Example:
            ```python
            # In factory, resolve already-created singleton
            container.register_scoped(
                UnitOfWork,
                lambda c: UnitOfWork(c.resolve_sync(Database))
            )
            ```
        """
        if interface in self._singletons:
            return self._singletons[interface]

        reg = self._registrations.get(interface)
        if reg is None:
            raise DependencyNotFoundError(interface)

        if reg.lifetime != Lifetime.SINGLETON:
            raise RuntimeError(
                f"resolve_sync() only works for singletons, "
                f"but {interface.__name__} has lifetime {reg.lifetime.value}"
            )

        raise RuntimeError(
            f"Singleton {interface.__name__} hasn't been created yet. "
            f"Use 'await container.resolve({interface.__name__})' first."
        )

    async def _resolve_singleton(self, reg: Registration[T]) -> T:
        """Resolve a singleton with thread-safe caching."""
        # Fast path: already cached
        if reg.interface in self._singletons:
            return self._singletons[reg.interface]

        # Slow path: create with lock
        async with self._singleton_lock:
            # Double-check after acquiring lock
            if reg.interface in self._singletons:
                return self._singletons[reg.interface]

            instance = await self._create_instance(reg)
            self._singletons[reg.interface] = instance
            return instance

    async def _create_instance(self, reg: Registration[T]) -> T:
        """Create an instance using the factory."""
        # Circular dependency detection
        if reg.interface in self._resolving:
            chain = list(self._resolving) + [reg.interface]
            raise CircularDependencyError(chain)

        self._resolving.add(reg.interface)
        try:
            if reg.is_async:
                return await reg.factory(self)
            else:
                # Run sync factory
                return reg.factory(self)
        finally:
            self._resolving.discard(reg.interface)

    @asynccontextmanager
    async def scope(self, scope_id: str) -> AsyncIterator[ScopedContainer]:
        """
        Create a scoped container for request/session lifetime.

        All scoped dependencies resolved within this context will be
        cached for the scope's lifetime and disposed when the scope exits.

        Args:
            scope_id: Unique identifier for the scope (for logging/debugging).

        Yields:
            ScopedContainer that can resolve scoped dependencies.

        Example:
            ```python
            async with container.scope("request-123") as scoped:
                session = await scoped.resolve(DbSession)
                # Use session...
            # Session automatically disposed
            ```
        """
        scoped = ScopedContainer(parent=self, scope_id=scope_id)
        try:
            yield scoped
        finally:
            await scoped.dispose()

    async def dispose(self) -> None:
        """
        Dispose all singleton instances.

        Call this when shutting down the application.
        """
        for interface, instance in list(self._singletons.items()):
            if isinstance(instance, Disposable):
                try:
                    await instance.dispose()
                    logger.debug(f"Disposed singleton {interface.__name__}")
                except Exception as e:
                    logger.error(f"Error disposing {interface.__name__}: {e}")
            elif isinstance(instance, SyncDisposable):
                try:
                    instance.dispose()
                    logger.debug(f"Disposed singleton {interface.__name__}")
                except Exception as e:
                    logger.error(f"Error disposing {interface.__name__}: {e}")

        self._singletons.clear()


@dataclass
class ScopedContainer:
    """
    Scoped container that inherits from parent.

    Caches scoped instances for the scope's lifetime.
    Automatically disposes all scoped instances when the scope exits.
    """

    parent: Container
    scope_id: str
    _instances: dict[type, Any] = field(default_factory=dict)
    _disposables: list[Disposable | SyncDisposable] = field(default_factory=list)
    _resolving: set[type] = field(default_factory=set)

    async def resolve(self, interface: type[T]) -> T:
        """
        Resolve a dependency within this scope.

        - Singletons: Delegated to parent container
        - Scoped: Cached for this scope's lifetime
        - Transient: Created fresh

        Args:
            interface: The type/protocol to resolve.

        Returns:
            The resolved instance.

        Raises:
            DependencyNotFoundError: If interface is not registered.
            CircularDependencyError: If circular dependency is detected.
        """
        reg = self.parent._registrations.get(interface)
        if reg is None:
            raise DependencyNotFoundError(interface)

        if reg.lifetime == Lifetime.SINGLETON:
            return await self.parent.resolve(interface)

        if reg.lifetime == Lifetime.SCOPED:
            return await self._resolve_scoped(reg)

        # Transient
        return await self._create_instance(reg)

    def resolve_sync(self, interface: type[T]) -> T:
        """
        Resolve synchronously (for use in sync factories).

        Works for:
        - Singletons that have been created
        - Scoped instances that have been created in this scope

        Args:
            interface: The type/protocol to resolve.

        Returns:
            The resolved instance.

        Raises:
            DependencyNotFoundError: If interface is not registered.
            RuntimeError: If instance hasn't been created yet.
        """
        # Check scoped cache first
        if interface in self._instances:
            return self._instances[interface]

        # Delegate to parent for singletons
        return self.parent.resolve_sync(interface)

    async def _resolve_scoped(self, reg: Registration[T]) -> T:
        """Resolve a scoped dependency with caching."""
        if reg.interface in self._instances:
            return self._instances[reg.interface]

        instance = await self._create_instance(reg)
        self._instances[reg.interface] = instance

        # Track for disposal
        if isinstance(instance, (Disposable, SyncDisposable)):
            self._disposables.append(instance)

        return instance

    async def _create_instance(self, reg: Registration[T]) -> T:
        """Create an instance using the factory."""
        # Circular dependency detection
        if reg.interface in self._resolving:
            chain = list(self._resolving) + [reg.interface]
            raise CircularDependencyError(chain)

        self._resolving.add(reg.interface)
        try:
            if reg.is_async:
                return await reg.factory(self.parent)
            else:
                return reg.factory(self.parent)
        finally:
            self._resolving.discard(reg.interface)

    async def dispose(self) -> None:
        """
        Dispose all scoped instances.

        Called automatically when exiting the scope context.
        Disposes in reverse order of creation.
        """
        for disposable in reversed(self._disposables):
            try:
                if isinstance(disposable, Disposable):
                    await disposable.dispose()
                else:
                    disposable.dispose()
            except Exception as e:
                logger.error(
                    f"Error disposing in scope {self.scope_id}: {e}"
                )

        self._disposables.clear()
        self._instances.clear()
        logger.debug(f"Disposed scope {self.scope_id}")


# Convenience function for creating a production container
def create_container() -> Container:
    """
    Create a new dependency injection container.

    Example:
        ```python
        container = create_container()
        container.register_singleton(Database, lambda c: Database(url))
        ```
    """
    return Container()
