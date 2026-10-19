"""Service abstractions — Protocols and control-plane collaborators."""

# Workspace Store
from supervisor.seams.services.authorized_workspace import authorized_hydrate, authorized_sync

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
    UnauthorizedError,
)
from supervisor.seams.services.authz_service import StoreAuthzService

# Session Gateway
# Memory Service Client
from supervisor.seams.services.memory_service_client import (
    memory_service_base_url,
    memory_service_credential,
    recall_memory_items,
    resolve_memory_scope,
    write_memory_item,
)

# Object Storage
from supervisor.seams.services.object_storage import (
    HeadResult,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectStorage,
    ObjectStorageError,
    ObjectTransient,
    PresignedUrl,
    PutResult,
)

# Session Workspace
from supervisor.seams.services.session_workspace import (
    SessionWorkspaceService,
    SessionWorkspaceState,
    WorkspaceConflictError,
    WorkspaceNotReadyError,
    tree_for_sandbox,
)

# Virtual Keys
from supervisor.seams.services.virtual_keys import (
    MintedVirtualKey,
    VirtualKeyClient,
)

# Workspace Hydrator
from supervisor.seams.services.workspace_hydrator import StoreWorkspaceHydrator, WorkspaceHydrator
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

__all__ = [
    "ALL_GRANTS",
    "AuthzService",
    "CasConflict",
    "ChecksumMismatch",
    "ForbiddenError",
    "Grant",
    "GrantDenied",
    "GrantStore",
    "HeadResult",
    "HydrateIncomplete",
    "MintedVirtualKey",
    "NotScopeMember",
    "ObjectIntegrityError",
    "ObjectNotFound",
    "ObjectStorage",
    "ObjectStorageError",
    "ObjectTransient",
    "PresignedUrl",
    "Principal",
    "PutResult",
    "SessionWorkspaceService",
    "SessionWorkspaceState",
    "StoreAuthzService",
    "StoreWorkspaceHydrator",
    "UnauthorizedError",
    "VirtualKeyClient",
    "WorkspaceConflictError",
    "WorkspaceFileRecord",
    "WorkspaceHydrator",
    "WorkspaceNotReadyError",
    "WorkspacePathEscape",
    "WorkspaceStore",
    "WorkspaceStoreError",
    "WorkspaceSyncReport",
    "WorkspaceTreeIO",
    "authorized_hydrate",
    "authorized_sync",
    "memory_service_base_url",
    "memory_service_credential",
    "recall_memory_items",
    "resolve_memory_scope",
    "tree_for_sandbox",
    "write_memory_item",
]
