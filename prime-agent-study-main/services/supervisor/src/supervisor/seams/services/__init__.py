"""Service abstractions — Protocols and control-plane collaborators."""

# Workspace Store
from supervisor.seams.services.workspace_store import (
    CasConflict,
    ChecksumMismatch,
    HydrateIncomplete,
    WorkspaceFileRecord,
    WorkspacePathEscape,
    WorkspaceStore,
    WorkspaceStoreError,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)

# Object Storage
from supervisor.seams.services.object_storage import (
    HeadResult,
    ObjectConflict,
    ObjectForbidden,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectStorage,
    ObjectStorageError,
    ObjectTransient,
    PresignedUrl,
    PutResult,
)

# Authorization
from supervisor.seams.services.authz import (
    ALL_GRANTS,
    AuthzService,
    ForbiddenError,
    Grant,
    GrantDenied,
    GrantStore,
    NotScopeMember,
    Principal,
    ScopeNotFound,
    UnauthorizedError,
)
from supervisor.seams.services.authz_service import (
    StoreAuthzService,
    as_authz_service,
)
from supervisor.seams.services.authorized_workspace import (
    authorized_exec,
    authorized_hydrate,
    authorized_sync,
)

# Virtual Keys
from supervisor.seams.services.virtual_keys import (
    MintedVirtualKey,
    VirtualKeyClient,
)

# Workspace Hydrator
from supervisor.seams.services.workspace_hydrator import (
    StoreWorkspaceHydrator,
    WorkspaceHydrator,
    as_workspace_hydrator,
)

# Session Workspace
from supervisor.seams.services.session_workspace import (
    SessionWorkspaceService,
    SessionWorkspaceState,
    WorkspaceConflictError,
    WorkspaceNotReadyError,
    tree_for_sandbox,
)

# Session Gateway
from supervisor.seams.services.session_gateway import attach_minted_gateway

# Memory Service Client
from supervisor.seams.services.memory_service_client import (
    memory_service_base_url,
    memory_service_credential,
    recall_memory_items,
    resolve_memory_scope,
    write_memory_item,
)

__all__ = [
    # Workspace Store
    "CasConflict",
    "ChecksumMismatch",
    "HydrateIncomplete",
    "WorkspaceFileRecord",
    "WorkspacePathEscape",
    "WorkspaceStore",
    "WorkspaceStoreError",
    "WorkspaceSyncReport",
    "WorkspaceTreeIO",
    # Object Storage
    "HeadResult",
    "ObjectConflict",
    "ObjectForbidden",
    "ObjectIntegrityError",
    "ObjectNotFound",
    "ObjectStorage",
    "ObjectStorageError",
    "ObjectTransient",
    "PresignedUrl",
    "PutResult",
    # Authorization
    "ALL_GRANTS",
    "AuthzService",
    "ForbiddenError",
    "Grant",
    "GrantDenied",
    "GrantStore",
    "NotScopeMember",
    "Principal",
    "ScopeNotFound",
    "StoreAuthzService",
    "UnauthorizedError",
    "as_authz_service",
    "authorized_exec",
    "authorized_hydrate",
    "authorized_sync",
    # Virtual Keys
    "MintedVirtualKey",
    "VirtualKeyClient",
    # Workspace Hydrator
    "StoreWorkspaceHydrator",
    "WorkspaceHydrator",
    "as_workspace_hydrator",
    # Session Workspace
    "SessionWorkspaceService",
    "SessionWorkspaceState",
    "WorkspaceConflictError",
    "WorkspaceNotReadyError",
    "tree_for_sandbox",
    # Session Gateway
    "attach_minted_gateway",
    # Memory Service Client
    "memory_service_base_url",
    "memory_service_credential",
    "recall_memory_items",
    "resolve_memory_scope",
    "write_memory_item",
]
