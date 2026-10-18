# Harness Architecture

The harness subsystem provides version resolution, compatibility checking, and session file materialization for agent harness configurations.

## Overview

The harness is a **versioned agent runtime abstraction layer** that provides:
- **Manifest-driven configuration**: Version-pinned specifications for agent behavior
- **Runtime abstraction**: Interface between supervisor and agent subprocess implementations
- **Session materialization**: Creates `.yal/` directories with all necessary configuration files

## Directory Structure

```
harness/
├── __init__.py              # Comprehensive exports
├── README.md                # This file
├── core/                    # Domain layer - types and interfaces
│   ├── __init__.py
│   ├── enums.py            # StrEnum definitions
│   ├── types.py            # Frozen dataclasses with __slots__
│   ├── errors.py           # Rich exception hierarchy
│   ├── protocols.py        # Protocol interfaces for DI
│   └── digests.py          # Content hashing utilities
├── storage/                 # Persistence layer
│   ├── __init__.py
│   ├── converters.py       # Dict <-> dataclass transformations
│   └── validators.py       # Path/env security validators
├── config/                  # Configuration layer
│   ├── __init__.py
│   └── settings.py         # Environment-based settings
├── services/                # Business logic layer
│   ├── __init__.py
│   ├── resolver.py         # Version resolution with caching
│   ├── compat_checker.py   # Runtime compatibility validation
│   └── spawn_planner.py    # Subprocess spawn plan building
└── materializers/           # Session file creation
    ├── __init__.py
    ├── session_files.py    # .yal/ directory materialization
    └── plug_bindings.py    # Configuration file reading
```

## Key Components

### Types (`core/types.py`)

All types use `@dataclass(frozen=True, slots=True)` for:
- **Immutability**: Values cannot be changed after creation
- **Memory efficiency**: `__slots__` reduces memory overhead
- **Thread safety**: Frozen objects are inherently thread-safe

```python
@dataclass(frozen=True, slots=True)
class HarnessManifest:
    harness_version: str
    protocol_version: int
    runtime: str
    requires_sandbox: bool
    mode: RuntimeMode
    # ... other fields
```

### Errors (`core/errors.py`)

Rich exception hierarchy with structured error data:

```python
class HarnessError(Exception):
    error_code: str = "HARNESS_ERROR"
    retryable: bool = False

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "detail": self.detail,
        }
```

Error categories:
- **Resolution**: `UnknownHarness`, `HarnessNotOffered`
- **Manifest**: `ManifestError`, `ManifestParseError`, `ManifestValidationError`
- **Compatibility**: `IncompatibleHarness`, `UnsupportedProtocol`, `SandboxRequired`
- **Spawn**: `SpawnError`, `MissingHarnessCli`, `InvalidGatewayConfig`
- **Security**: `PathTraversalAttempt`, `InvalidEnvironmentValue`

### Protocols (`core/protocols.py`)

Protocol interfaces enable dependency injection and testing:

```python
@runtime_checkable
class HarnessResolverProtocol(Protocol):
    async def resolve(self, harness_version: str, *, for_create: bool = False) -> HarnessManifest: ...
    async def list_available(self, *, include_deprecated: bool = False) -> list[str]: ...
    async def publish(self, manifest: HarnessManifest, *, status: str = "active") -> None: ...
```

### Services

#### HarnessResolver (`services/resolver.py`)

Thread-safe version resolution with multiple fallback sources:

```python
class HarnessResolver:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        versions_dir: Path | str | None = None,
        catalog: HarnessCatalog | None = None,
    ) -> None:
        self._cache: dict[str, HarnessManifest] = {}
        self._cache_lock = asyncio.Lock()  # Thread-safe caching
```

Resolution order:
1. Thread-safe memory cache
2. Database (HarnessVersionRow table)
3. In-memory catalog
4. Directory (YAML files)

#### CompatibilityChecker (`services/compat_checker.py`)

Validates manifests against runtime environment:
- Protocol version support
- Runtime registration
- Required commands
- Sandbox requirements
- Approval support

#### SpawnPlanner (`services/spawn_planner.py`)

Builds CLI arguments and environment for harness subprocess:
- Gateway mode (OpenAI-compatible API)
- Direct mode (provider/model)

### Configuration (`config/settings.py`)

Centralized environment-based configuration:

```python
@dataclass(frozen=True, slots=True)
class HarnessSettings:
    supervisor_base_url: str
    harness_cli: str | None
    contracts_dir: str | None
    memory_service_url: str | None
    memory_tenant: str
    memory_namespace: str
    default_protocol_versions: frozenset[int]
```

Environment variables:
- `SUPERVISOR_BASE_URL`: Base URL for supervisor API
- `HARNESS_CLI`: Path to harness CLI executable
- `YAL_CONTRACTS_DIR` / `CONTRACTS_DIR`: Path to contracts directory
- `MEMORY_SERVICE_URL`: URL for durable memory service

## Usage Examples

### Resolving a Harness Version

```python
from supervisor.harness import HarnessResolver, HarnessCatalog, UnknownHarness

catalog = HarnessCatalog()
resolver = HarnessResolver(catalog=catalog)

try:
    manifest = await resolver.resolve("coding-v1", for_create=True)
    print(f"Resolved: {manifest.harness_version}")
except UnknownHarness as e:
    print(f"Unknown version: {e.harness_version}")
```

### Checking Compatibility

```python
from supervisor.harness import CompatibilityChecker, SandboxRequired

checker = CompatibilityChecker()

try:
    checker.check(manifest, sandbox_available=False)
except SandboxRequired as e:
    print(f"Sandbox required: {e.harness_version}")
```

### Materializing Session Files

```python
from supervisor.harness import materialize_harness_binds

paths = materialize_harness_binds(
    session_dir="/path/to/session",
    pins=session_pins,
    seams=runtime_seams,
    session_id="sess_123",
    tenant_id="tenant_abc",
)

print(f"Created: {paths.pins_json}")
print(f"Created: {paths.system_prompt_txt}")
```

## Design Principles

1. **Immutability First**: All types are frozen dataclasses
2. **Thread Safety**: Async locks protect shared state
3. **Rich Errors**: Structured exceptions with error codes
4. **Protocol-Based DI**: Interfaces enable easy mocking
5. **Security Hardening**: Path traversal and input validation
6. **Centralized Config**: No hardcoded values
7. **Comprehensive Logging**: Structured logging throughout
