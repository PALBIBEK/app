"""Domain operations layer for session management.

Provides specialized operations that bridge external providers
with the journal event system:
- SubagentOps: Subagent lifecycle management
- MemoryOps: Memory compaction/recall operations
- SessionTree: Child session tree rebuild from journal
- Compaction helpers: Unpaired compaction detection
"""

from __future__ import annotations

from supervisor.session.operations.compaction_state import (
    unpaired_compaction_starts,
)
from supervisor.session.operations.memory_ops import (
    CompactMemoryOutcome,
    MemoryOps,
    RecallMemoryOutcome,
)
from supervisor.session.operations.subagent_ops import (
    DEFAULT_MAX_DEPTH,
    SubagentOps,
)
from supervisor.session.operations.tree import (
    SessionTree,
    build_session_tree,
    unpaired_subagent_starts,
)

__all__ = [
    # Subagent
    "SubagentOps",
    "DEFAULT_MAX_DEPTH",
    # Memory
    "MemoryOps",
    "CompactMemoryOutcome",
    "RecallMemoryOutcome",
    # Tree
    "SessionTree",
    "build_session_tree",
    "unpaired_subagent_starts",
    # Compaction
    "unpaired_compaction_starts",
]
