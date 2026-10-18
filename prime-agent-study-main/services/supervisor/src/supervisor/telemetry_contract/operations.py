"""Operation enums — GenAI registry vs yal platform (Phase1 §3.1)."""

from __future__ import annotations

from enum import StrEnum


class GenAIOperation(StrEnum):
    INVOKE_AGENT = "invoke_agent"
    INVOKE_WORKFLOW = "invoke_workflow"
    PLAN = "plan"
    CHAT = "chat"
    EXECUTE_TOOL = "execute_tool"
    EMBEDDINGS = "embeddings"
    SEARCH_MEMORY = "search_memory"
    TEXT_COMPLETION = "text_completion"
    CREATE_AGENT = "create_agent"


class PlatformOperation(StrEnum):
    """Phase 1 projected platform ops only. Sandbox is Phase 1b — not listed."""

    TURN = "turn"
    APPROVAL = "approval"
    COMPACTION = "compaction"
    JOB = "job"


# Reject list if a client/projector tries to emit sandbox ops early.
PHASE1_FORBIDDEN_PLATFORM_OPS = frozenset(
    {
        "sandbox.create",
        "sandbox.exec",
        "sandbox.snapshot",
        "sandbox.destroy",
    }
)
