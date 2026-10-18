"""Session management for the supervisor service.

This module provides session lifecycle management including:
- Session creation and termination
- Message handling and abort
- Recovery and resume
- Subagent operations
- Memory compaction/recall
- Live event streaming

Architecture:
    session/
    ├── core/        # Domain layer - pure types & interfaces
    ├── storage/     # Persistence layer - registry, outbox
    ├── config/      # Configuration layer - settings
    ├── services/    # Business logic layer - recovery, fanout, lifecycle
    └── operations/  # Domain operations - subagent, memory, tree

Example:
    from supervisor.session import (
        SessionManager,
        SessionMode,
        ResumeState,
        SessionError,
    )

    manager = SessionManager(...)
    session_id = await manager.create(tenant_id="t1", harness_version="coding-v1")
"""

from __future__ import annotations

# =============================================================================
# Enums
# =============================================================================
from supervisor.session.core.enums import (
    ChildActivity,
    JobActivity,
    ResumeState,
    SessionMode,
    SessionStatus,
    mode_for_harness,
    mode_for_manifest,
)

# =============================================================================
# Types
# =============================================================================
from supervisor.session.core.types import (
    BeginOutcome,
    ChildRow,
    JobRow,
    RegistryMetrics,
    ResumeDecision,
    ResumeResult,
    SessionInfo,
    SessionMetrics,
    SessionTree,
    ToolInvokeOutcome,
)

# =============================================================================
# Errors
# =============================================================================
from supervisor.session.core.errors import (
    MemoryNotConfigured,
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionError,
    SessionNotFound,
    SessionTerminated,
    SubagentNotConfigured,
    ToolInvokeNotConfigured,
    _auto_context,
)

# =============================================================================
# Protocols
# =============================================================================
from supervisor.session.core.protocols import (
    RecoveryProtocol,
    SessionManagerProtocol,
    SessionRegistryProtocol,
    SessionRowView,
)

# =============================================================================
# Storage
# =============================================================================
from supervisor.session.storage.registry import SessionRegistry
from supervisor.session.storage.outbox import (
    ToolOutbox,
    idempotency_key,
    result_to_wire,
    wire_to_result,
)
from supervisor.session.storage.converters import (
    session_info_to_dict,
    resume_decision_to_dict,
    child_row_to_dict,
    job_row_to_dict,
)

# =============================================================================
# Config
# =============================================================================
from supervisor.session.config.settings import (
    SessionSettings,
    get_settings,
)

# =============================================================================
# Services
# =============================================================================
from supervisor.session.services.recovery import (
    Recovery,
    decide_unpaired,
    pinned_manifest,
)
from supervisor.session.services.fanout import (
    LiveHub,
    LiveSubscription,
    is_dropped_sentinel,
)
from supervisor.session.services.lifecycle import (
    reap_idle,
    drain_for_deploy,
)
from supervisor.session.services.workspace import (
    workspaces_root,
    session_workspace,
)

# =============================================================================
# Operations
# =============================================================================
from supervisor.session.operations.subagent_ops import (
    SubagentOps,
    DEFAULT_MAX_DEPTH,
)
from supervisor.session.operations.memory_ops import (
    MemoryOps,
    CompactMemoryOutcome,
    RecallMemoryOutcome,
)
from supervisor.session.operations.tree import (
    build_session_tree,
    unpaired_subagent_starts,
)
from supervisor.session.operations.compaction_state import (
    unpaired_compaction_starts,
)

# =============================================================================
# Manager (main facade)
# =============================================================================
from supervisor.session.manager import SessionManager

__all__ = [
    # Manager
    "SessionManager",
    # Enums
    "ChildActivity",
    "JobActivity",
    "ResumeState",
    "SessionMode",
    "SessionStatus",
    "mode_for_harness",
    "mode_for_manifest",
    # Types
    "BeginOutcome",
    "ChildRow",
    "JobRow",
    "RegistryMetrics",
    "ResumeDecision",
    "ResumeResult",
    "SessionInfo",
    "SessionMetrics",
    "SessionTree",
    "ToolInvokeOutcome",
    # Errors
    "MemoryNotConfigured",
    "RecoveryBlocked",
    "SessionAwaitingApproval",
    "SessionError",
    "SessionNotFound",
    "SessionTerminated",
    "SubagentNotConfigured",
    "ToolInvokeNotConfigured",
    "_auto_context",
    # Protocols
    "RecoveryProtocol",
    "SessionManagerProtocol",
    "SessionRegistryProtocol",
    "SessionRowView",
    # Storage
    "SessionRegistry",
    "ToolOutbox",
    "idempotency_key",
    "result_to_wire",
    "wire_to_result",
    "session_info_to_dict",
    "resume_decision_to_dict",
    "child_row_to_dict",
    "job_row_to_dict",
    # Config
    "SessionSettings",
    "get_settings",
    # Services
    "Recovery",
    "decide_unpaired",
    "pinned_manifest",
    "LiveHub",
    "LiveSubscription",
    "is_dropped_sentinel",
    "reap_idle",
    "drain_for_deploy",
    "workspaces_root",
    "session_workspace",
    # Operations
    "SubagentOps",
    "DEFAULT_MAX_DEPTH",
    "MemoryOps",
    "CompactMemoryOutcome",
    "RecallMemoryOutcome",
    "build_session_tree",
    "unpaired_subagent_starts",
    "unpaired_compaction_starts",
]
