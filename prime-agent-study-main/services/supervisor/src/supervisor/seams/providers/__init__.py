"""Concrete provider implementations for all seams.

Providers are organized by seam type:
- tools/     → ToolProvider implementations
- gateway/   → ModelGatewayClient implementations
- prompt/    → PromptAssembler implementations
- subagent/  → SubagentTransport implementations
- memory/    → ContextMemoryProvider implementations
- workspace/ → WorkspaceStore implementations
- storage/   → ObjectStorage implementations
- authz/     → GrantStore implementations
"""

from __future__ import annotations

from supervisor.seams.providers.authz import (
    MemoryGrantStore,
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.gateway import (
    FakeModelGatewayClient,
    HttpModelGatewayClient,
    HttpVirtualKeyClient,
    maybe_virtual_key_client,
)
from supervisor.seams.providers.memory import (
    BasicCompactionProvider,
    CompositeContextMemoryProvider,
    DurableRecallProvider,
    MarkdownRecallProvider,
    McpSemanticRecallProvider,
    NullContextMemoryProvider,
)
from supervisor.seams.providers.prompt import (
    StaticPromptAssembler,
)
from supervisor.seams.providers.storage import (
    MemoryObjectStorage,
    S3ObjectStorage,
    create_object_storage,
)
from supervisor.seams.providers.subagent import (
    LAB_ECHO_PROVIDER,
    LEGACY_MEMORY_ALIAS,
    AcpSubagentProvider,
    LabEchoSubagentProvider,
    NestedSessionSubagentProvider,
)

# Re-export from subfolders for backwards compatibility
from supervisor.seams.providers.tools import (
    CompositeToolProvider,
    McpToolProvider,
    NullToolProvider,
)
from supervisor.seams.providers.workspace import (
    MemoryTreeIO,
    MemoryWorkspaceStore,
    create_object_storage_for_workspace,
    create_workspace_hydrator,
    create_workspace_store,
)

__all__ = [
    # Tools
    "NullToolProvider",
    "McpToolProvider",
    "CompositeToolProvider",
    # Gateway
    "FakeModelGatewayClient",
    "HttpModelGatewayClient",
    "HttpVirtualKeyClient",
    "maybe_virtual_key_client",
    # Prompt
    "StaticPromptAssembler",
    # Subagent
    "LabEchoSubagentProvider",
    "LAB_ECHO_PROVIDER",
    "LEGACY_MEMORY_ALIAS",
    "AcpSubagentProvider",
    "NestedSessionSubagentProvider",
    # Memory
    "NullContextMemoryProvider",
    "BasicCompactionProvider",
    "MarkdownRecallProvider",
    "McpSemanticRecallProvider",
    "DurableRecallProvider",
    "CompositeContextMemoryProvider",
    # Workspace
    "MemoryTreeIO",
    "MemoryWorkspaceStore",
    "create_workspace_store",
    "create_workspace_hydrator",
    "create_object_storage_for_workspace",
    # Storage
    "MemoryObjectStorage",
    "S3ObjectStorage",
    "create_object_storage",
    # Authz
    "MemoryGrantStore",
    "create_grant_store",
    "create_authz_service",
]
