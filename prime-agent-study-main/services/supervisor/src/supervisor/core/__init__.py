"""
Core domain module for the supervisor service.

This module contains the rewritten core components with:
- Clean domain models with validation
- Protocol-based abstractions
- Single-responsibility services
- Comprehensive error handling
- Built-in observability

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │  SessionCoordinator (Orchestration Only)                    │
    │  - Thin coordinator, delegates to focused services          │
    │  - Transaction boundaries                                   │
    │  - Error handling and recovery                             │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌──────────┬──────────┬──────────┬──────────┬────────────────┐
    │ Registry │ Lifecycle│ Runtime  │ Turn     │ Recovery       │
    │ Service  │ Service  │ Manager  │ Executor │ Service        │
    └──────────┴──────────┴──────────┴──────────┴────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  Domain Models (Immutable, Validated)                       │
    │  - Session, SessionState, SessionConfig                     │
    │  - Turn, TurnState, Message                                │
    │  - Events (RunStart, TurnEnd, etc.)                        │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  Protocols (Abstractions)                                   │
    │  - SessionStore, RuntimeProvider, EventSink                │
    │  - ToolProvider, ModelGateway                              │
    └─────────────────────────────────────────────────────────────┘

Design Principles:
    1. Single Responsibility: Each class does one thing well
    2. Dependency Injection: All dependencies via constructor
    3. Immutable Models: State changes create new instances
    4. Protocol-based: Depend on abstractions, not implementations
    5. Fail Fast: Validate early, fail with clear errors
    6. Observable: Built-in tracing and metrics
"""

__version__ = "2.0.0"
